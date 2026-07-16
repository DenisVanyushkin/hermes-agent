from fam import car

class FakeAuth:
    def __init__(self, script): self.script = list(script); self.n = 0
    def get_app_code(self, i, s): return "CODE"
    def get_app_token(self, i, s, c): return "APPTOK"
    def get_slid_user_token(self, tok, login, pw, sms_code=None, captcha_sid=None, captcha_code=None):
        r = self.script[self.n]; self.n += 1; return r
    def get_user_id(self, slid): return ("SLNET", 9999999999, "UID")

def test_bootstrap_happy_path():
    auth = FakeAuth([(1, {"user_token": "SLID"})])
    store = car.bootstrap(auth, "15526", "sec", "login", "pw",
                          prompt_sms=lambda: "", prompt_captcha=lambda u: ("", ""))
    assert store["slid_token"] == "SLID"
    assert store["user_id"] == "UID" and store["slnet_token"] == "SLNET"

def test_bootstrap_sms_branch():
    auth = FakeAuth([(2, {"phone": "+7***"}), (1, {"user_token": "SLID"})])
    got = {}
    def sms(): got["asked"] = True; return "123456"
    store = car.bootstrap(auth, "15526", "sec", "login", "pw",
                          prompt_sms=sms, prompt_captcha=lambda u: ("", ""))
    assert got.get("asked") and store["slid_token"] == "SLID"

def test_bootstrap_captcha_branch():
    auth = FakeAuth([(0, {"captchaSid": "SID", "captchaImg": "http://img"}),
                     (1, {"user_token": "SLID"})])
    got = {}
    def cap(url): got["url"] = url; return ("SID", "abcd")
    store = car.bootstrap(auth, "15526", "sec", "login", "pw",
                          prompt_sms=lambda: "", prompt_captcha=cap)
    assert got["url"] == "http://img" and store["slid_token"] == "SLID"
