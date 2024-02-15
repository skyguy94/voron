Symlink the ~/printer_data/config folder to the appropriate subfolder.

Ex:
```
rm -fr ~/printer_data/config #Ensure this folder doesn't have anything you want to keep first.
git checkout <repo> ~/voron
ln -s ~/voron/vt_1399 ~/printer_data/config
```
