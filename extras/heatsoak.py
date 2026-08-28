import logging
import statistics
from collections import deque


class HeatSoak:
    cmd_HEAT_SOAK_help = "Drive a heater and wait for a second sensor to settle"
    cmd_STOP_HEAT_SOAK_help = "Stop an in-progress heat soak without running callbacks"
    cmd_CANCEL_HEAT_SOAK_help = "Stop an in-progress heat soak and run its cancel callback"
    cmd_SKIP_HEAT_SOAK_help = "Skip the remaining soak, running the complete callback when the heater arrives"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.check_interval = config.getfloat('check_interval', 1., above=0.)
        self.temp_smooth_time = config.getfloat('temp_smooth_time', 4., above=0.)
        self.rate_smooth_time = config.getfloat('rate_smooth_time', 120., above=0.)
        self.report_interval = config.getfloat('report_interval', 90., above=0.)
        self.complete_gcode = gcode_macro.load_template(config, 'complete_gcode', '')
        self.cancel_gcode = gcode_macro.load_template(config, 'cancel_gcode', '')
        self.timer = None
        self._reset()
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("virtual_sdcard:reset_file", self._handle_reset_file)
        self.gcode.register_command('HEAT_SOAK', self.cmd_HEAT_SOAK, desc=self.cmd_HEAT_SOAK_help)
        self.gcode.register_command('STOP_HEAT_SOAK', self.cmd_STOP_HEAT_SOAK, desc=self.cmd_STOP_HEAT_SOAK_help)
        self.gcode.register_command('CANCEL_HEAT_SOAK', self.cmd_CANCEL_HEAT_SOAK,
                                    desc=self.cmd_CANCEL_HEAT_SOAK_help)
        self.gcode.register_command('SKIP_HEAT_SOAK', self.cmd_SKIP_HEAT_SOAK, desc=self.cmd_SKIP_HEAT_SOAK_help)

    def _reset(self):
        self.stage = 'idle'
        self.heater_name = None
        self.soaker_name = None
        self.target_temp = 0.
        self.min_soak_temp = 0.
        self.target_rate = 0.3
        self.flat_rate = 0.1
        self.flat_hold = 0.
        self.soak_floor = 0.
        self.timeout = 0.
        self.elapsed = 0.
        self.flat_time = 0.
        self.next_report = 0.
        self.resume_trigger = False
        self.sample_window = max(1, int(self.temp_smooth_time / self.check_interval))
        self.rate_window = max(2, int(self.rate_smooth_time / self.check_interval))
        self.samples = deque(maxlen=self.sample_window)
        self.smoothed = deque(maxlen=self.rate_window)
        self.slope = None
        self.soak_temp = None

    def _handle_ready(self):
        self.timer = self.reactor.register_timer(self._tick)

    def _handle_reset_file(self):
        if self.stage in ('heating', 'soaking'):
            self._respond("Heat soak stopped, the print file was reset.")
            self.stop()

    def get_status(self, eventtime):
        return {'stage': self.stage, 'soak_temp': self.soak_temp, 'slope': self.slope,
                'elapsed': self.elapsed, 'remaining': max(0., self.timeout - self.elapsed),
                'flat_time': self.flat_time, 'target_temp': self.target_temp,
                'min_soak_temp': self.min_soak_temp, 'resume_trigger': self.resume_trigger}

    def _respond(self, msg):
        self.gcode.respond_info(msg)

    def _label(self, name):
        label = name.split(' ')[-1]
        return {'heater_bed': 'bed', 'extruder': 'hotend',
                'chamber_heater': 'chamber'}.get(label, label.replace('_', ' '))

    def _elapsed_text(self, seconds):
        minutes = int(round(seconds / 60.))
        if minutes < 1:
            return "under a minute"
        if minutes < 60:
            return "%d min" % (minutes,)
        return "%dh %02dm" % (minutes // 60, minutes % 60)

    def _temperature(self, name):
        return self.printer.lookup_object(name).get_status(self.reactor.monotonic())['temperature']

    def _heater(self, name):
        return self.printer.lookup_object('heaters').lookup_heater(name)

    def _heater_status(self, name):
        return self._heater(name).get_status(self.reactor.monotonic())

    def _slope_of(self, series):
        slope, _ = statistics.linear_regression(range(len(series)), series)
        return slope * 60. / self.check_interval

    def _push(self, temp):
        self.samples.append(temp)
        if len(self.samples) < self.sample_window:
            return
        self.soak_temp = statistics.fmean(self.samples)
        self.smoothed.append(self.soak_temp)
        if len(self.smoothed) == self.rate_window:
            self.slope = self._slope_of(self.smoothed)

    def stop(self):
        self.stage = 'idle'
        if self.timer is not None:
            self.reactor.update_timer(self.timer, self.reactor.NEVER)

    def _finish(self, outcome, from_command=False):
        self.stage = outcome
        if self.timer is not None:
            self.reactor.update_timer(self.timer, self.reactor.NEVER)
        template = self.complete_gcode if outcome == 'done' else self.cancel_gcode
        script = template.render()
        if script.strip():
            try:
                if from_command:
                    self.gcode.run_script_from_command(script)
                else:
                    self.gcode.run_script(script)
            except Exception:
                logging.exception("heatsoak %s gcode" % (outcome,))
        return self.reactor.NEVER

    def cmd_HEAT_SOAK(self, gcmd):
        self._reset()
        self.soaker_name = gcmd.get('SOAKER')
        self.heater_name = gcmd.get('HEATER', None)
        self.target_temp = gcmd.get_float('TARGET', 0.)
        self.min_soak_temp = gcmd.get_float('SOAK_TEMP', 0.)
        self.target_rate = gcmd.get_float('RATE', 0.3)
        self.flat_rate = gcmd.get_float('FLAT_RATE', 0.1)
        self.flat_hold = gcmd.get_float('FLAT_HOLD', 0.)
        self.soak_floor = gcmd.get_float('SOAK_FLOOR', 0.)
        self.timeout = gcmd.get_float('TIMEOUT', 30., above=0.) * 60.
        self._push(self._temperature(self.soaker_name))
        if self.heater_name and self.target_temp:
            self._heater(self.heater_name).set_temp(self.target_temp)
            self.stage = 'heating'
            self._respond("Heat soak started. Warming the %s to %.0f °C, then holding until the %s settles."
                          % (self._label(self.heater_name), self.target_temp,
                             self._label(self.soaker_name)))
        else:
            self.stage = 'soaking'
            self._respond("Heat soak started. Holding until the %s settles."
                          % (self._label(self.soaker_name),))
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def cmd_STOP_HEAT_SOAK(self, gcmd):
        if self.stage not in ('heating', 'soaking'):
            gcmd.respond_info("No heat soak is running.")
            return
        phase = "warming up" if self.stage == 'heating' else "soaking"
        heater = self.heater_name
        self.stop()
        if heater:
            target = self._heater_status(heater).get('target', 0.)
            gcmd.respond_info("Heat soak stopped while %s. The %s is still set to %.0f °C."
                              % (phase, self._label(heater), target))
        else:
            gcmd.respond_info("Heat soak stopped while %s." % (phase,))

    def cmd_CANCEL_HEAT_SOAK(self, gcmd):
        if self.stage in ('heating', 'soaking'):
            self._finish('cancel', from_command=True)

    def cmd_SKIP_HEAT_SOAK(self, gcmd):
        if self.stage == 'heating':
            self.resume_trigger = True
        elif self.stage == 'soaking':
            self._respond("Soak skipped after %s." % (self._elapsed_text(self.elapsed),))
            self._finish('done', from_command=True)

    def _report(self, eventtime, msg):
        if self.elapsed >= self.next_report:
            self.next_report = self.elapsed + self.report_interval
            self._respond(msg)
        return eventtime + self.check_interval

    def _tick(self, eventtime):
        if self.printer.is_shutdown() or self.stage not in ('heating', 'soaking'):
            return self.reactor.NEVER
        if self.gcode.get_mutex().test():
            return eventtime + 0.05
        self.elapsed += self.check_interval
        self._push(self._temperature(self.soaker_name))
        if self.stage == 'heating':
            heater_temp = self._heater_status(self.heater_name)['temperature']
            if heater_temp < self.target_temp:
                return self._report(eventtime, "Warming up: %s at %.0f of %.0f °C, %s so far."
                                    % (self._label(self.heater_name), heater_temp,
                                       self.target_temp, self._elapsed_text(self.elapsed)))
            if self.resume_trigger:
                self._respond("The %s reached %.0f °C after %s. Soak skipped."
                              % (self._label(self.heater_name), self.target_temp,
                                 self._elapsed_text(self.elapsed)))
                return self._finish('done')
            self._respond("The %s reached %.0f °C after %s. Soaking now."
                          % (self._label(self.heater_name), self.target_temp,
                             self._elapsed_text(self.elapsed)))
            self.stage = 'soaking'
            self.elapsed = 0.
            self.next_report = 0.
        return self._soak(eventtime)

    def _soak(self, eventtime):
        if self.elapsed >= self.timeout:
            self._respond("Heat soak hit its %s limit before the %s settled."
                          % (self._elapsed_text(self.timeout), self._label(self.soaker_name)))
            return self._finish('cancel')
        if self.slope is None or self.soak_temp is None:
            return self._report(eventtime, "Soaking: taking the first %s readings, %s in."
                                % (self._label(self.soaker_name),
                                   self._elapsed_text(self.elapsed)))
        self.flat_time = self.flat_time + self.check_interval if abs(self.slope) <= self.flat_rate else 0.
        plateaued = (self.flat_hold > 0. and self.flat_time >= self.flat_hold
                     and (self.soak_floor <= 0. or self.soak_temp >= self.soak_floor))
        below_target = self.min_soak_temp > 0. and self.soak_temp < self.min_soak_temp
        if self.slope > self.target_rate or (below_target and not plateaued):
            return self._report(eventtime, "Soaking: %s at %.0f °C, %+.1f °C/min. %s in, up to %s left."
                                % (self._label(self.soaker_name), self.soak_temp, self.slope,
                                   self._elapsed_text(self.elapsed),
                                   self._elapsed_text(self.timeout - self.elapsed)))
        if below_target:
            self._respond("The %s levelled off at %.0f °C after %s, short of %.0f °C. Moving on."
                          % (self._label(self.soaker_name), self.soak_temp,
                             self._elapsed_text(self.elapsed), self.min_soak_temp))
        else:
            self._respond("Heat soak done after %s. The %s is at %.0f °C."
                          % (self._elapsed_text(self.elapsed), self._label(self.soaker_name),
                             self.soak_temp))
        return self._finish('done')


def load_config(config):
    return HeatSoak(config)
