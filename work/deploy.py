import paramiko
import sys
import time

host = "114.132.71.132"
user = "ubuntu"
password = "Wenhua2309"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=password, timeout=10)

def run(cmd, timeout=30):
    print(f"$ {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out: print(out)
    if err and exit_code != 0: print(f"ERR: {err}")
    return exit_code, out

# Step 1: Check existing environment
print("=== 1/6 Checking server environment ===")
run("python3 --version")
run("pip3 --version")
run("git --version")

# Step 2: Install system dependencies
print("\n=== 2/6 Installing system dependencies ===")
run("sudo apt-get update -qq", 60)
run("sudo apt-get install -y -qq python3-pip python3-venv nginx git", 120)

# Step 3: Clone the project
print("\n=== 3/6 Cloning project from GitHub ===")
run("cd ~ && git clone https://github.com/ruifengc166-boop/zhengxuan.git 2>/dev/null || (cd zhengxuan && git pull)", 60)

# Step 4: Install Python dependencies
print("\n=== 4/6 Installing Python dependencies ===")
run("cd ~/zhengxuan && pip3 install --break-system-packages -r requirements.txt -q", 120)

# Step 5: Run setup to initialize database
print("\n=== 5/6 Initializing database and admin account ===")
run("mkdir -p ~/zhengxuan/work/data && cd ~/zhengxuan && python3 setup.py", 30)

# Step 6: Create systemd service
print("\n=== 6/6 Creating systemd service ===")
service = """[Unit]
Description=政宣智作 - AI Video Creation Platform
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/zhengxuan/work
ExecStart=/usr/bin/python3 /home/ubuntu/zhengxuan/work/app.py
Restart=on-failure
RestartSec=5
Environment=JWT_SECRET=zhengxuan-prod-jwt-secret-2026
Environment=JWT_SECRET=zhengxuan-prod-jwt-secret-2026
Environment=PORT=8000
Environment=FLASK_DEBUG=0
Environment=FLASK_DEBUG=0

[Install]
WantedBy=multi-user.target
"""
run(f"echo '{service}' | sudo tee /etc/systemd/system/zhengxuan.service", 10)
run("sudo systemctl daemon-reload", 10)
run("sudo systemctl enable zhengxuan", 10)
run("sudo systemctl restart zhengxuan", 10)

time.sleep(3)

# Check if service is running
code, out = run("sudo systemctl status zhengxuan --no-pager | head -10")
if "active (running)" in out:
    print("\nOK Service is running!")
else:
    print("\nWARN Service status:")
    print(out)
    code2, out2 = run("sudo journalctl -u zhengxuan -n 20 --no-pager")
    print(out2)

# Test the app
print("\n=== Testing ===")
run("curl -s http://localhost:8000/api/health")
run("curl -s -o /dev/null -w 'Admin panel: %{http_code}' http://localhost:8000/admin/")
run("curl -s -o /dev/null -w 'Front page: %{http_code}' http://localhost:8000/")

print("\n\n=== Configuring Nginx ===")
nginx_conf = """server {
    listen 80;
    server_name _;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        client_max_body_size 500M;
    }
}
"""
run(f"echo '{nginx_conf}' | sudo tee /etc/nginx/sites-available/zhengxuan", 10)
run("sudo rm -f /etc/nginx/sites-enabled/default", 5)
run("sudo ln -sf /etc/nginx/sites-available/zhengxuan /etc/nginx/sites-enabled/", 5)
run("sudo nginx -t", 5)
run("sudo systemctl restart nginx", 5)
run("sudo ufw allow 80/tcp 2>/dev/null || true", 5)

print("\n" + "=" * 50)
print("  ✅ DEPLOYMENT COMPLETE!")
print("=" * 50)
print(f"  Frontend: http://{host}/")
print(f"  Admin:    http://{host}/admin/")
print(f"  Account:  18800000000 / admin123")
print()
print("  Commands for future management:")
print(f"  ssh ubuntu@{host}")
print("  sudo systemctl restart zhengxuan  # restart app")
print("  sudo journalctl -u zhengxuan -f    # view logs")
print("  cd ~/zhengxuan && git pull        # update code")
print("=" * 50)

client.close()
