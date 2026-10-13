# System-wide install (Gentoo & co.):   ./configure && ninja -C build && sudo ninja -C build install
# Nothing gets compiled — Void is Python. This puts the app in <prefix>/share/void, `void` in <prefix>/bin, plus a menu
# entry and icon. Each user then runs `void setup` once for their own voice + speech models (they live in ~/.local/share).
project('void', version: '0.1.0', meson_version: '>=0.60', license: 'MIT')

share = get_option('datadir') / 'void'

install_subdir('app', install_dir: share, exclude_directories: ['__pycache__', 'static/__pycache__'])
install_data('install.sh', install_dir: share, install_mode: 'rwxr-xr-x')
install_data('bin/void', install_dir: get_option('bindir'), install_mode: 'rwxr-xr-x')
install_data('data/void.desktop', install_dir: get_option('datadir') / 'applications')
install_data('app/static/icon.png', install_dir: get_option('datadir') / 'icons' / 'hicolor' / '512x512' / 'apps',
             rename: 'void.png')
