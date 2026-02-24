#!/usr/bin/env bash
set -euo pipefail

rsync -a files/ "${ROOTFS_DIR}/"

install -d -m 0755 "${ROOTFS_DIR}/opt/potato" "${ROOTFS_DIR}/opt/potato"/{app,bin,models,state,config,llama,nginx,systemd}

chmod +x "${ROOTFS_DIR}"/opt/potato/bin/*.sh

install -m 0644 "${ROOTFS_DIR}/opt/potato/systemd/potato.service" "${ROOTFS_DIR}/etc/systemd/system/potato.service"
install -m 0644 "${ROOTFS_DIR}/opt/potato/systemd/potato-firstboot.service" "${ROOTFS_DIR}/etc/systemd/system/potato-firstboot.service"
install -m 0644 "${ROOTFS_DIR}/opt/potato/systemd/potato-runtime-reset.service" "${ROOTFS_DIR}/etc/systemd/system/potato-runtime-reset.service"

install -m 0644 "${ROOTFS_DIR}/opt/potato/nginx/potato.conf" "${ROOTFS_DIR}/etc/nginx/sites-available/potato"
# Create runtime-valid symlink inside rootfs (not an absolute pi-gen build path).
ln -sf /etc/nginx/sites-available/potato "${ROOTFS_DIR}/etc/nginx/sites-enabled/potato"
rm -f "${ROOTFS_DIR}/etc/nginx/sites-enabled/default"

printf 'potato\n' > "${ROOTFS_DIR}/etc/hostname"
hosts_tmp="$(mktemp)"
awk '
  BEGIN { printed = 0 }
  /^127\.0\.1\.1[[:space:]]/ {
    if (!printed) {
      print "127.0.1.1 potato.local potato"
      printed = 1
    }
    next
  }
  { print }
  END {
    if (!printed) {
      print "127.0.1.1 potato.local potato"
    }
  }
' "${ROOTFS_DIR}/etc/hosts" > "${hosts_tmp}"
mv "${hosts_tmp}" "${ROOTFS_DIR}/etc/hosts"

if [ -f "${ROOTFS_DIR}/etc/avahi/avahi-daemon.conf" ]; then
  if grep -q '^[#[:space:]]*host-name=' "${ROOTFS_DIR}/etc/avahi/avahi-daemon.conf"; then
    sed -i "s/^[#[:space:]]*host-name=.*/host-name=potato/" "${ROOTFS_DIR}/etc/avahi/avahi-daemon.conf"
  else
    printf '\nhost-name=potato\n' >> "${ROOTFS_DIR}/etc/avahi/avahi-daemon.conf"
  fi
fi

on_chroot <<'EOF'
if ! getent group potato >/dev/null 2>&1; then
  groupadd --system potato
fi
if ! id -u potato >/dev/null 2>&1; then
  useradd --system --home /opt/potato --shell /usr/sbin/nologin --gid potato potato
fi
if getent group video >/dev/null 2>&1; then
  usermod -a -G video potato
fi

if [ ! -x /opt/potato/venv/bin/python ]; then
  python3 -m venv /opt/potato/venv
fi
/opt/potato/venv/bin/pip install --upgrade pip
/opt/potato/venv/bin/pip install -r /opt/potato/app/requirements.txt

chown -R potato:potato /opt/potato

cat > /etc/sudoers.d/potato-runtime-reset <<'SUDOERS'
potato ALL=(root) NOPASSWD: /bin/systemctl start --no-block potato-runtime-reset.service
potato ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block potato-runtime-reset.service
SUDOERS
chmod 0440 /etc/sudoers.d/potato-runtime-reset

systemctl enable potato-firstboot.service potato.service nginx avahi-daemon ssh
EOF
