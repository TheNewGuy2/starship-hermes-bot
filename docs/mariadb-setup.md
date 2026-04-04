# MariaDB (Native + systemd) setup on Ubuntu Server 24.x

This is a minimal, local-only MariaDB setup for a mini PC. It binds to localhost and creates a `starship` database/user.

## 1) Install and enable MariaDB

```bash
sudo apt update
sudo apt install -y mariadb-server
sudo systemctl enable --now mariadb
```

## 2) Secure the install

```bash
sudo mariadb-secure-installation
```

## 3) Create database and user

Replace `REPLACE_ME` with a strong password.

```bash
sudo mariadb -e "
CREATE DATABASE IF NOT EXISTS starship CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'starship'@'localhost' IDENTIFIED BY 'REPLACE_ME';
GRANT ALL PRIVILEGES ON starship.* TO 'starship'@'localhost';
FLUSH PRIVILEGES;
"
```

## 4) Bind to localhost

Edit `/etc/mysql/mariadb.conf.d/50-server.cnf` and ensure:

```
bind-address = 127.0.0.1
```

Then restart:

```bash
sudo systemctl restart mariadb
```

## 5) Verify

```bash
sudo systemctl status mariadb
```

## Optional: connect test

```bash
mariadb -u starship -p -h 127.0.0.1 starship
```
