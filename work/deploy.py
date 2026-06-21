import paramiko, time

host = "114.132.71.132"
user = "ubuntu"
password = "Wenhua2309"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=password, timeout=10)

def run(cmd, timeout=60):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out: print(out[:300])
    if err and exit_code != 0: print("ERR:", err[:200])
    return exit_code, out

print("=== Updating server ===")
run("update-zhengxuan", 60)

time.sleep(3)
print("\n=== Verifying ===")
run("sudo systemctl is-active zhengxuan")
run("curl -s http://localhost:8000/api/health")
run("curl -s -o /dev/null -w 'Admin: %{http_code}\n' http://localhost:8000/admin/")

client.close()
print("\n=== Cloud server updated! ===")
print("http://114.132.71.132/")
