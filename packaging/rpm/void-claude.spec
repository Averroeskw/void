# Void for Fedora (and relatives): meson's system-wide install, packaged. Built by CI on every release:
#   rpmbuild -bb --define "ver 1.2.3" packaging/rpm/void-claude.spec   (with void-1.2.3.tar.gz in SOURCES)
# After installing, each user runs `void setup` once (their voice + speech models).
%global __brp_python_bytecompile %{nil}
%global debug_package %{nil}

Name:           void-claude
Version:        %{ver}
Release:        1%{?dist}
Summary:        Voice-first desktop companion for Claude Code
License:        MIT
URL:            https://github.com/PLAYER99-void/void
Source0:        void-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  meson
Requires:       python3 python3-gobject python3-cairo gtk3 webkit2gtk4.1 gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good pulseaudio-utils curl uv
Recommends:     gtk-layer-shell espeak-ng alsa-utils libnotify grim slurp wl-clipboard maim xclip playerctl brightnessctl

%description
Say "hey Void" and a little orb on your desktop (or a notch in your screen's edge) does the work in Claude Code,
then tells you how it went. His voice and speech recognition run on your computer. After installing, each user
runs "void setup" once to download their voice and speech models.

%prep
%autosetup -n void-%{version}

%build
%meson
%meson_build

%install
%meson_install

%files
%license LICENSE
%{_bindir}/void
%{_datadir}/void/
%{_datadir}/applications/void.desktop
%{_datadir}/icons/hicolor/512x512/apps/void.png

%post
echo "Void is installed. Each user: run 'void setup' once (downloads your voice and speech models), then 'void'."
echo "He needs Claude Code too: https://docs.anthropic.com/en/docs/claude-code"
