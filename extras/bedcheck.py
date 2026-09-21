import json
import os
import shutil
import subprocess
import tempfile
import time

ANSWERS = ('clear', 'occupied', 'no_plate')


class BedCheck:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.python = os.path.expanduser(config.get('python', '~/klippy-env/bin/python'))
        self.script = os.path.expanduser(
            config.get('script', '~/voron/scripts/bedcam/bedcheck.py'))
        self.profile_name = config.get('profile', 'trident')
        self.reference = os.path.expanduser(config.get('reference', '~/bedcam-data/reference'))
        self.train_dir = os.path.expanduser(config.get('train_dir', '~/bedcam-data/train'))
        self.park = config.get('park', 'fl')
        self.check_z = config.getfloat('check_z', 25., above=0.)
        self.threshold = config.getint('threshold', 200, minval=1)
        self.marking_threshold = config.getfloat('marking_threshold', 0.5)
        self.settle = config.getfloat('settle', 1.5, above=0.)
        self.timeout = config.getfloat('timeout', 60., above=0.)
        self.pos_tolerance = config.getfloat('pos_tolerance', 1.0, above=0.)
        self.train = config.getboolean('train', True)
        self.pending = None
        self.result = {'verdict': 'UNKNOWN', 'blob': 0, 'regions': 0, 'marking': 0.0,
                       'last_object_height': -1.0, 'last_filename': '',
                       'last_status': '', 'label': '', 'pending': ''}
        self.gcode.register_command('BED_CHECK', self.cmd_BED_CHECK,
                                    desc=self.cmd_BED_CHECK_help)
        self.gcode.register_command('BED_CHECK_PREPARE', self.cmd_BED_CHECK_PREPARE,
                                    desc=self.cmd_BED_CHECK_PREPARE_help)
        self.gcode.register_command('BED_CHECK_CONFIRM', self.cmd_BED_CHECK_CONFIRM,
                                    desc=self.cmd_BED_CHECK_CONFIRM_help)
        self.gcode.register_command('BED_CHECK_DISMISS', self.cmd_BED_CHECK_DISMISS,
                                    desc=self.cmd_BED_CHECK_DISMISS_help)

    def _profile(self):
        path = os.path.join(os.path.dirname(self.script), 'profiles',
                            self.profile_name + '.json')
        with open(path) as fh:
            return json.load(fh)

    def get_status(self, eventtime):
        return dict(self.result)

    def _run(self, args, gcmd):
        out = tempfile.TemporaryFile(mode='w+')
        proc = subprocess.Popen([self.python, self.script] + args,
                                stdout=out, stderr=subprocess.STDOUT,
                                cwd=os.path.dirname(self.script))
        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + self.timeout
        while proc.poll() is None:
            if reactor.monotonic() > deadline:
                proc.kill()
                out.close()
                raise gcmd.error('BED_CHECK timed out after %.0f s' % self.timeout)
            reactor.pause(reactor.monotonic() + 0.2)
        out.seek(0)
        text = out.read().strip()
        out.close()
        return proc.returncode, text

    def _lights(self, profile, level):
        for tmpl in profile['lights'].get('chamber', []):
            self.gcode.run_script_from_command(tmpl.format(level=level))

    def _restore_lights(self, profile):
        for name, level in profile.get('lights_restore', {}).items():
            for tmpl in profile['lights'].get(name, []):
                self.gcode.run_script_from_command(tmpl.format(level=level))

    def _command(self, msg):
        try:
            self.gcode.run_script_from_command('RESPOND TYPE=command MSG="%s"' % msg)
        except Exception:
            pass

    def _end_prompt(self):
        self._command('action:prompt_end')

    def _prompt(self):
        verdict = self.result.get('verdict', 'ERROR')
        self._end_prompt()
        self._command('action:prompt_begin Bed check')
        self._command('action:prompt_text Camera says %s. Largest blob %d px, marking '
                      'score %.2f. What is actually on the bed?'
                      % (verdict, self.result.get('blob', 0),
                         self.result.get('marking', 0.0)))
        self._command('action:prompt_button Bed is clear|BED_CHECK_CONFIRM ANSWER=clear|primary')
        self._command('action:prompt_button Parts on bed|BED_CHECK_CONFIRM ANSWER=occupied|warning')
        self._command('action:prompt_button No build plate|BED_CHECK_CONFIRM ANSWER=no_plate|error')
        self._command('action:prompt_footer_button Skip|BED_CHECK_DISMISS|secondary')
        self._command('action:prompt_show')

    def _promote(self, srcdir):
        z = float(self.result.get('z', self.check_z))
        park = self.result.get('park', self.park)
        os.makedirs(self.reference, exist_ok=True)
        shutil.copyfile(os.path.join(srcdir, 'lit.jpg'),
                        os.path.join(self.reference, 'sweep_%s_z%03.0f_c100.jpg' % (park, z)))
        shutil.copyfile(os.path.join(srcdir, 'unlit.jpg'),
                        os.path.join(self.reference, 'sweep_%s_z%03.0f_c000.jpg' % (park, z)))
        with open(os.path.join(self.reference, 'reference.json'), 'w') as fh:
            json.dump({'source': srcdir, 'z': z, 'park': park,
                       'set': time.time(), 'by': 'confirm'}, fh, indent=1)

    def _verify_pose(self, gcmd, z):
        toolhead = self.printer.lookup_object('toolhead')
        status = toolhead.get_status(self.printer.get_reactor().monotonic())
        if 'xyz' not in status['homed_axes']:
            raise gcmd.error('BED_CHECK needs a homed machine, homed_axes is %r'
                             % (status['homed_axes'],))
        profile = self._profile()
        px, py = profile['parks'][self.park]
        pos = toolhead.get_position()
        if (abs(pos[0] - px) > self.pos_tolerance
                or abs(pos[1] - py) > self.pos_tolerance):
            raise gcmd.error('BED_CHECK expects the toolhead parked at %s '
                             '(X%.1f Y%.1f), found X%.1f Y%.1f'
                             % (self.park, px, py, pos[0], pos[1]))
        if abs(pos[2] - z) > 0.5:
            raise gcmd.error('BED_CHECK expects Z%.2f, found Z%.2f' % (z, pos[2]))
        return profile

    cmd_BED_CHECK_PREPARE_help = 'Query the previous job so the bed can be cleared of it'

    def cmd_BED_CHECK_PREPARE(self, gcmd):
        workdir = tempfile.mkdtemp(prefix='bedcheck-')
        path = os.path.join(workdir, 'lastjob.json')
        self._run(['--profile', self.profile_name, 'lastjob', '--json-out', path], gcmd)
        info = {}
        try:
            with open(path) as fh:
                info = json.load(fh)
        except (IOError, OSError, ValueError):
            pass
        height = info.get('object_height')
        self.result['last_object_height'] = float(height) if height is not None else -1.0
        self.result['last_filename'] = info.get('filename') or ''
        self.result['last_status'] = info.get('status') or ''
        shown = ('%.1f mm' % self.result['last_object_height']
                 if self.result['last_object_height'] >= 0 else 'unknown')
        gcmd.respond_info('BED_CHECK_PREPARE last job %s (%s), tallest object %s'
                          % (self.result['last_filename'] or 'none',
                             self.result['last_status'] or 'unknown', shown))

    cmd_BED_CHECK_help = 'Camera check that the bed is clear and the build plate is present'

    def cmd_BED_CHECK(self, gcmd):
        z = gcmd.get_float('Z', self.check_z, above=0.)
        require_clear = gcmd.get_int('REQUIRE_CLEAR', 1)
        train = gcmd.get_int('TRAIN', 1 if self.train else 0)
        reference = os.path.expanduser(gcmd.get('REFERENCE', self.reference))
        profile = self._verify_pose(gcmd, z)
        reactor = self.printer.get_reactor()
        workdir = tempfile.mkdtemp(prefix='bedcheck-')
        lit = os.path.join(workdir, 'lit.jpg')
        unlit = os.path.join(workdir, 'unlit.jpg')
        result_path = os.path.join(workdir, 'result.json')
        try:
            self.gcode.run_script_from_command('STOP_LED_EFFECTS')
            self._lights(profile, 1.0)
            reactor.pause(reactor.monotonic() + self.settle)
            code, text = self._run(['--profile', self.profile_name, 'grab',
                                    '--out', lit, '--lock'], gcmd)
            if code != 0:
                raise gcmd.error('BED_CHECK could not capture: %s' % text)
            self._lights(profile, 0.0)
            reactor.pause(reactor.monotonic() + self.settle)
            code, text = self._run(['--profile', self.profile_name, 'grab',
                                    '--out', unlit], gcmd)
            if code != 0:
                raise gcmd.error('BED_CHECK could not capture: %s' % text)
        finally:
            self._restore_lights(profile)
            self._run(['--profile', self.profile_name, 'unlock'], gcmd)

        args = ['--profile', self.profile_name, 'verdict',
                '--reference', reference, '--lit', lit, '--unlit', unlit,
                '--z', '%.2f' % z, '--park', self.park,
                '--threshold', '%d' % self.threshold,
                '--marking-threshold', '%.3f' % self.marking_threshold,
                '--json-out', result_path]
        savedir = None
        if train:
            savedir = os.path.join(self.train_dir, time.strftime('%Y%m%d-%H%M%S'))
            args += ['--save', savedir]
        code, text = self._run(args, gcmd)
        try:
            with open(result_path) as fh:
                self.result.update(json.load(fh))
        except (IOError, OSError, ValueError):
            self.result['verdict'] = 'ERROR'
        if code == 2:
            raise gcmd.error('BED_CHECK failed: %s' % text)

        self.result['label'] = ''
        self.pending = savedir if (savedir and os.path.isdir(savedir)) else None
        self.result['pending'] = self.pending or ''
        verdict = self.result.get('verdict', 'ERROR')
        gcmd.respond_info('BED_CHECK %s   blob %d px   marking %.2f   threshold %d'
                          % (verdict, self.result.get('blob', 0),
                             self.result.get('marking', 0.0), self.threshold))
        if self.pending:
            self._prompt()
        if require_clear and verdict != 'CLEAR':
            if verdict == 'NO_PLATE':
                raise gcmd.error('No build plate detected. Refusing to continue.')
            raise gcmd.error('Bed is not clear, %d px over threshold %d. '
                             'Refusing to continue.'
                             % (self.result.get('blob', 0), self.threshold))

    cmd_BED_CHECK_CONFIRM_help = 'Record what was really on the bed and refresh the reference'

    def cmd_BED_CHECK_CONFIRM(self, gcmd):
        answer = gcmd.get('ANSWER', '').strip().lower()
        if answer not in ANSWERS:
            raise gcmd.error('ANSWER must be one of %s' % (', '.join(ANSWERS),))
        self._end_prompt()
        pending = self.pending
        if not pending or not os.path.isdir(pending):
            gcmd.respond_info('BED_CHECK_CONFIRM: no capture waiting to be labelled')
            return
        verdict = self.result.get('verdict', 'ERROR')
        with open(os.path.join(pending, 'label.json'), 'w') as fh:
            json.dump({'answer': answer, 'verdict': verdict,
                       'blob': self.result.get('blob', 0),
                       'marking': self.result.get('marking', 0.0),
                       'threshold': self.threshold, 'when': time.time()}, fh, indent=1)
        self.result['label'] = answer
        expected = {'clear': 'CLEAR', 'occupied': 'OCCUPIED',
                    'no_plate': 'NO_PLATE'}[answer]
        if answer == 'clear':
            self._promote(pending)
            gcmd.respond_info('Labelled clear. Reference refreshed from this capture.')
        else:
            gcmd.respond_info('Labelled %s. Reference left alone.' % answer)
        if expected != verdict:
            gcmd.respond_info('Camera said %s, you said %s. Recorded as a miss in %s'
                              % (verdict, expected, pending))
        self.pending = None
        self.result['pending'] = ''

    cmd_BED_CHECK_DISMISS_help = 'Close the bed check prompt without recording an answer'

    def cmd_BED_CHECK_DISMISS(self, gcmd):
        self._end_prompt()
        self.pending = None
        self.result['pending'] = ''
        gcmd.respond_info('Bed check left unlabelled.')


def load_config(config):
    return BedCheck(config)
