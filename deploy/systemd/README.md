# systemd deployment

可选。若希望 QMD 随 Linux 开机启动，可将 `company-qmd.service` 安装到 `/etc/systemd/system/`。

```bash
sudo cp deploy/systemd/company-qmd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now company-qmd
sudo systemctl status company-qmd
```

如果 `qmd` 安装在仅用户可见的 Bun/npm 路径，请把 `ExecStart` 改成实际绝对路径。
