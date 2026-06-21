import urllib.request, json, sys
BASE="http://localhost:3000"
ok=0
fail=0

def test(method, path, data=None, headers=None):
    global ok, fail
    url = BASE + path
    hd = {"Content-Type": "application/json"}
    if headers:
        hd.update(headers)
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=hd, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print("  OK " + method + " " + path + " -> 200")
            ok += 1
            return result
    except urllib.error.HTTPError as e:
        er = json.loads(e.read())
        print("  FAIL " + method + " " + path + " -> " + str(e.code) + ": " + er.get("error",""))
        fail += 1
        return None
    except Exception as e:
        print("  FAIL " + method + " " + path + " -> " + str(e))
        fail += 1
        return None

print("1. Database persistence test")
test("GET", "/api/health")
d = test("GET", "/api/projects")
if d:
    print("   Projects count: " + str(len(d.get("projects",[]))))

print()
print("2. Login authentication test")
r = test("POST", "/api/auth/login", {"phone":"18800000000","password":"123456"})
token = ""
if r and r.get("success"):
    token = r["token"]
    print("   Token OK: " + token[:30] + "...")
    user = r["user"]
    print("   User: " + user["name"] + " (" + user["role"] + ")")

print()
print("3. Auth middleware test")
print("   Without token:")
test("GET", "/api/auth/me")
print("   With token:")
test("GET", "/api/auth/me", headers={"Authorization": "Bearer " + token})

print()
print("4. AI generation API test")
r2 = test("POST", "/api/generate/images", {"prompt":"test"}, headers={"Authorization": "Bearer " + token})
if r2:
    print("   Task ID: " + r2.get("taskId",""))
    print("   Status: " + r2.get("status",""))

print()
print("5. Admin API database test")
test("GET", "/api/admin/dashboard", headers={"Authorization": "Bearer " + token})
d2 = test("GET", "/api/admin/users", headers={"Authorization": "Bearer " + token})
if d2:
    print("   Total users: " + str(d2["total"]))
d3 = test("GET", "/api/admin/orgs", headers={"Authorization": "Bearer " + token})
if d3:
    print("   Total orgs: " + str(len(d3["orgs"])))
d4 = test("GET", "/api/admin/billing/stats", headers={"Authorization": "Bearer " + token})
if d4:
    print("   Revenue: " + str(d4["totalRecharge"]) + "  Expense: " + str(d4["totalConsume"]) + "  Balance: " + str(d4["balance"]))

print()
print("6. Frontend page test")
try:
    with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
        print("   Frontend: " + str(resp.status))
        ok += 1
except Exception as e:
    print("   Frontend: FAIL - " + str(e))
    fail += 1
try:
    with urllib.request.urlopen(BASE + "/admin/", timeout=5) as resp:
        print("   Admin panel: " + str(resp.status))
        ok += 1
except Exception as e:
    print("   Admin panel: FAIL - " + str(e))
    fail += 1

print()
print("Results: OK=" + str(ok) + " FAIL=" + str(fail))
sys.exit(0 if fail == 0 else 1)
