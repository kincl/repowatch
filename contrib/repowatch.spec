%global commit0_date 20150922
%global commit0 1322d4fe6a0dc0887092f0d9baf58726a4af2c9f
%global shortcommit0 %(c=%{commit0}; echo ${c:0:7})

%{!?__python2: %global __python2 /usr/bin/python2}

Name:   repowatch
Version: %{commit0_date}git%{shortcommit0}
Release: 1%{?dist}
Summary: Watches Gerrit and GitLab and checks out git repo updates

License: Apache
URL: https://github.com/kincl/repowatch
Source0:  https://github.com/kincl/%{name}/archive/%{commit0}.tar.gz#/%{name}-%{shortcommit0}.tar.gz

BuildArch:      noarch
BuildRequires:  python2-devel
Requires: git python-argparse PyYAML python-daemon python-paramiko

%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd
BuildRequires: systemd
%else
Requires(post): chkconfig
Requires(preun): chkconfig
# This is for /sbin/service
Requires(preun): initscripts
Requires(postun): initscripts
%endif


%description
Watches Gerrit and GitLab and checks out git repo updates


%prep
%setup -qn %{name}-%{commit0}

%build
%{__python2} setup.py build

%install
%{__python2} setup.py install --single-version-externally-managed -O1 --root=$RPM_BUILD_ROOT --record=INSTALLED_FILES

%{__install} -d -m755 %{buildroot}/%{_sysconfdir}/repowatch
%{__install}    -m644 etc/repowatch.conf %{buildroot}/%{_sysconfdir}/repowatch/repowatch.conf
%{__install}    -m644 etc/projects.yaml %{buildroot}/%{_sysconfdir}/repowatch/projects.yaml

%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
%{__install} -d -m755 %{buildroot}/%{_unitdir}
%{__install}    -m644 contrib/repowatch.service %{buildroot}/%{_unitdir}/repowatch.service
%else
%{__install} -d -m755 %{buildroot}/%{_sysconfdir}/rc.d/init.d
%{__install}    -m755 contrib/repowatch.init %{buildroot}/%{_sysconfdir}/rc.d/init.d/repowatch
%endif
%{__install} -d -m755 %{buildroot}/%{_sysconfdir}/sysconfig
%{__install}    -m644 contrib/repowatch.sysconfig %{buildroot}/%{_sysconfdir}/sysconfig/repowatch

%clean
rm -rf $RPM_BUILD_ROOT

%files -f INSTALLED_FILES
%defattr(-,root,root)
%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
%{_unitdir}/repowatch.service
%else
%{_sysconfdir}/rc.d/init.d/repowatch
%endif
%config(noreplace) %{_sysconfdir}/sysconfig/repowatch
%config(noreplace) %{_sysconfdir}/repowatch/repowatch.conf
%config(noreplace) %{_sysconfdir}/repowatch/projects.yaml

%post
%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
%systemd_post repowatch.service
%else
/sbin/chkconfig --add repowatch
%endif

%preun
%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
%systemd_preun repowatch.service
%else
if [ $1 -eq 0 ] ; then
    /sbin/service repowatch stop >/dev/null 2>&1
    /sbin/chkconfig --del repowatch
fi
%endif

%postun
%if 0%{?fedora} >= 17 || 0%{?rhel} >= 7
%systemd_postun_with_restart repowatch.service
%else
if [ "$1" -ge "1" ] ; then
    /sbin/service repowatch condrestart >/dev/null 2>&1 || :
fi
%endif
