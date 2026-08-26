import time
import os
import json
import re
import random
import traceback

import requests
from seleniumbase import SB

# ================= 环境配置 =================
# 仅在未设置时使用默认 DISPLAY，兼容 GitHub Actions / Docker / VNC。
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":1"

if "XAUTHORITY" not in os.environ and os.path.exists("/home/headless/.Xauthority"):
    os.environ["XAUTHORITY"] = "/home/headless/.Xauthority"

print(f"[DEBUG] Env DISPLAY: {os.environ.get('DISPLAY')}")
print(f"[DEBUG] Env XAUTHORITY: {os.environ.get('XAUTHORITY')}")

# ================= 配置区域 =================
PROXY_URL = os.getenv("PROXY", "").strip()
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
NUM = os.getenv("NUM", "").strip()
COOKIE = os.getenv("COOKIE", "").strip()

LOGIN_URL = "https://hub.weirdhost.xyz/auth/login"
URL_APP_PANEL = f"https://hub.weirdhost.xyz/server/{NUM}" if NUM else ""

RENEW_BUTTON = '//button[contains(normalize-space(.), "연장하기")]'
TIMESTAMP_PATTERN = r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}'
# 冷却提示的真实文案是"N일/N시간/N분 후에 연장할 수 있어요"（N天/N小时/N分钟后可以续期），
# 之前代码里判断"可以续期"用的是"지금 연장이 가능해요"这句话，
# 但实际页面从来不会显示这句话，导致判断逻辑一直失效。
COOLDOWN_PATTERN = r'(\d+)\s*(일|시간|분)\s*후에\s*연장할\s*수\s*있어요'


class WeirdHostRenewal:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.base_dir, "artifacts")
        os.makedirs(self.screenshot_dir, exist_ok=True)

    def log(self, msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [INFO] {msg}", flush=True)

    def human_wait(self, min_s=6, max_s=10):
        time.sleep(random.uniform(min_s, max_s))

    def validate_config(self):
        missing = []
        if not NUM:
            missing.append("NUM")
        if not COOKIE:
            missing.append("COOKIE")

        if missing:
            raise RuntimeError(
                "缺少环境变量：" + ", ".join(missing)
                + "。请在 GitHub Secrets/环境变量中配置。"
            )

    def safe_screenshot(self, sb, filename):
        path = os.path.join(self.screenshot_dir, filename)
        try:
            sb.save_screenshot(path)
            return path
        except Exception as e:
            self.log(f"⚠️ 截图失败：{e}")
            return None

    def send_telegram_notify(self, message, photo_path=None):
        if not TG_TOKEN or not TG_CHAT_ID:
            self.log("⚠️ 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过推送。")
            return

        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                with open(photo_path, "rb") as f:
                    response = requests.post(
                        url,
                        data={"chat_id": TG_CHAT_ID, "caption": message},
                        files={"photo": f},
                        timeout=30,
                    )
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                response = requests.post(
                    url,
                    data={"chat_id": TG_CHAT_ID, "text": message},
                    timeout=30,
                )

            response.raise_for_status()
            self.log("✅ TG 推送已发送")
        except Exception as e:
            self.log(f"❌ TG 推送失败: {e}")

    def get_page_timestamp(self, sb):
        """安全提取页面中的到期时间，找不到时返回“未找到”。"""
        try:
            page_text = sb.get_text("body")
            match = re.search(TIMESTAMP_PATTERN, page_text, re.IGNORECASE)
            if match:
                return match.group(0)
        except Exception as e:
            self.log(f"⚠️ 读取到期时间失败：{e}")

        self.log("⚠️ 页面中未找到 YYYY-MM-DD HH:MM:SS 格式的时间")
        return "未找到"

    def open_with_retry(self, sb, url, name, retries=3, reconnect_time=5):
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                self.log(f"🌐 正在访问{name}（第 {attempt}/{retries} 次）...")
                sb.uc_open_with_reconnect(url, reconnect_time=reconnect_time)
                time.sleep(3)

                if sb.get_current_url():
                    self.log(f"✅ {name}已打开")
                    return True
            except Exception as e:
                last_error = e
                self.log(f"⚠️ {name}打开失败：{e}")

            if attempt < retries:
                wait_time = 5 * attempt
                self.log(f"⏳ {wait_time} 秒后重试...")
                time.sleep(wait_time)

        raise RuntimeError(f"{name}连续 {retries} 次打开失败：{last_error}")

    def move_mouse_human(self, sb):
        """保留原脚本的页面交互预热逻辑。"""
        try:
            for _ in range(3):
                sb.slow_click("body")
                time.sleep(random.uniform(0.5, 1.2))
        except Exception as e:
            self.log(f"⚠️ 页面交互预热失败，继续执行：{e}")

    def get_turnstile_token(self, sb):
        try:
            # uc=True 这种反检测模式下，execute_script 不会自动把代码包成函数，
            # 顶层直接写 return 会报 "Illegal return statement"。
            # 用立即执行函数 (function(){...})() 包一层，不管哪种模式下 return 都合法。
            token = sb.execute_script("""
                (function() {
                    let tokens = [];
                    document.querySelectorAll('input, textarea').forEach(el => {
                        let v = el.value || "";
                        if (v.length > 50) tokens.push(v);
                    });

                    if (tokens.length) {
                        tokens.sort((a, b) => b.length - a.length);
                        return tokens[0];
                    }
                    return "";
                })();
            """)

            if token and len(token) > 50:
                return token
        except Exception as e:
            self.log(f"⚠️ Token扫描异常: {e}")

        return None

    def wait_cloudflare(self, sb, max_attempts=2, poll_seconds=90):
        """等待页面挑战结束；超时后不直接判定续期失败，由后续面板结果确认。"""
        cf_words = [
            "verify you are human",
            "just a moment",
            "checking your browser",
            "troubleshoot",
        ]

        for attempt in range(1, max_attempts + 1):
            self.log(f"⏳ Cloudflare 检查，第 {attempt}/{max_attempts} 次")
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                self.log(f"⚠️ 点击挑战控件失败：{e}")

            deadline = time.time() + poll_seconds
            while time.time() < deadline:
                try:
                    page_lower = sb.get_page_source().lower()
                    if not any(word in page_lower for word in cf_words):
                        self.log("✅ Cloudflare 挑战页面已消失")
                        return True
                except Exception as e:
                    self.log(f"⚠️ 检查 Cloudflare 状态失败：{e}")

                token = self.get_turnstile_token(sb)
                if token:
                    self.log(f"✅ 获取到 Turnstile Token，长度={len(token)}")
                    return True

                time.sleep(3)

            if attempt < max_attempts:
                try:
                    self.log("⚠️ 等待超时，尝试备用处理...")
                    self.move_mouse_human(sb)
                    sb.uc_gui_handle_captcha()
                except Exception as e:
                    self.log(f"⚠️ 备用处理失败：{e}")
                time.sleep(8)

        self.log("⚠️ Cloudflare 等待超时，继续通过最终页面状态判断是否成功")
        return False

    def inject_cookie(self, sb):
        if not COOKIE:
            raise RuntimeError("COOKIE 为空，无法登录。")

        sb.add_cookie({
            "name": "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d",
            "value": COOKIE,
            "domain": "hub.weirdhost.xyz",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
            "expires": int(time.time()) + 3600 * 24 * 365,
        })
        self.log("✅ Cookie 注入成功")

    def renewal_available(self, sb):
        """
        判断是否可以续期：在整个页面文字里搜索"N天/N小时/N分钟后可以续期"这句冷却提示。
        - 找到了：说明还在冷却期，还不能续期，返回 False（并把剩余时间记下来打日志）
        - 没找到：说明冷却已经结束，可以续期，返回 True

        注意：不能用"续期按钮是否可见"来判断——冷却期间按钮其实也一直显示在页面上，
        只是点了没用，"看得见"不等于"能点"，这是之前误判的根源。
        """
        try:
            page_text = sb.get_text("body")
        except Exception as e:
            self.log(f"⚠️ 读取页面文字失败：{e}")
            page_text = ""

        match = re.search(COOLDOWN_PATTERN, page_text)
        if match:
            remaining = f"{match.group(1)}{match.group(2)}"
            self.log(f"⏳ 检测到冷却提示：{remaining}后可以续期 -> 时间尚未达到")
            return "cooldown"

        # 没搜到冷却提示，不代表就一定能续期——也可能是页面根本没加载出真实内容
        # （比如还卡在 Cloudflare 验证页、或者没登录成功）。
        # 这里再确认一下页面上该有的东西（到期时间关键字/续期按钮）是不是真的存在，
        # 都没有的话，说明当前页面状态不对，不能就此认定为"可以续期"。
        page_looks_valid = ("연장하기" in page_text) or ("유통기한" in page_text)
        if not page_looks_valid:
            self.log("⚠️ 页面中既没有冷却提示，也没有找到到期时间/续期按钮相关文字，判断为页面状态异常")
            return "unknown"

        self.log("✅ 页面中未检测到冷却提示，判断为可以续期")
        return "available"

    def click_renew(self, sb):
        self.log("⏳ 等待续期按钮...")
        sb.wait_for_element_visible(RENEW_BUTTON, timeout=30)
        sb.scroll_to(RENEW_BUTTON)
        time.sleep(1)

        try:
            sb.click(RENEW_BUTTON)
        except Exception as e:
            self.log(f"⚠️ 普通点击失败，尝试 JS 点击：{e}")
            sb.execute_script("""
                const buttons = [...document.querySelectorAll('button')];
                const btn = buttons.find(b => b.innerText.includes('연장하기'));
                if (!btn) throw new Error('未找到 연장하기 按钮');
                btn.click();
            """)

        self.log("✅ 已点击 연장하기")

    def run(self):
        self.log("=" * 50)
        self.log("🚀 WeirdHost 自动续期任务开始")
        self.log("=" * 50)

        self.validate_config()
        self.log(f"🎯 服务器编号：{NUM}")
        self.log(f"🔗 目标面板：{URL_APP_PANEL}")
        self.log("🎯 正在启动 Chrome 浏览器...")

        with SB(
            uc=True,
            headed=True,
            headless=False,
            xvfb=False,
            chromium_arg=(
                "--no-sandbox,"
                "--disable-dev-shm-usage,"
                "--window-position=0,0,"
                "--start-maximized"
            ),
            proxy=PROXY_URL if PROXY_URL else None,
        ) as sb:
            try:
                self.log("✅ 浏览器已启动")

                # 1. 检测出口 IP（失败不影响续期）
                self.log("🌍 正在检测出口 IP...")
                try:
                    sb.open("https://api.ipify.org?format=json")
                    body = sb.get_text("body")
                    match = re.search(r"\{.*\}", body)
                    if not match:
                        raise ValueError(f"IP 接口返回异常：{body[:200]}")

                    ip_val = json.loads(match.group(0)).get("ip", "Unknown")
                    if "." in ip_val:
                        parts = ip_val.split(".")
                        masked_ip = f"{parts[0]}.{parts[1]}.***.{parts[-1]}"
                    else:
                        masked_ip = ip_val
                    self.log(f"✅ 当前出口 IP: {masked_ip}")
                except Exception as e:
                    self.log(f"⚠️ IP 检测跳过：{e}")

                # 2. 打开登录页
                self.open_with_retry(sb, LOGIN_URL, "登录页面")
                self.log("⏳ 等待页面 JS 渲染...")
                time.sleep(5)

                # 3. Cloudflare 页面检查
                self.wait_cloudflare(sb, max_attempts=2, poll_seconds=60)

                # 4. 注入 Cookie 后直接打开目标面板
                self.inject_cookie(sb)
                self.open_with_retry(sb, URL_APP_PANEL, "服务器面板")
                self.human_wait(6, 10)
                sb.scroll_to_bottom()
                time.sleep(2)

                # 5. 判断是否可以续期
                self.log("⏳ 开始检查续期状态...")
                renewal_status = self.renewal_available(sb)

                if renewal_status == "cooldown":
                    timestamp = self.get_page_timestamp(sb)
                    screenshot = self.safe_screenshot(sb, "cooldown.png")
                    msg = (
                        "⏳ 时间尚未达到，暂不能续期\n"
                        f"当前服务器到期时间：{timestamp}"
                    )
                    self.log(msg)
                    self.send_telegram_notify(msg, screenshot)
                    return

                if renewal_status == "unknown":
                    screenshot = self.safe_screenshot(sb, "unknown_state.png")
                    msg = (
                        "❌ 页面状态异常，未能确认续期按钮/到期时间\n"
                        "可能是 Cloudflare 验证没通过，或者 Cookie 已失效导致没登录成功，"
                        "本次跳过，不去点击不存在的按钮。请查看截图确认。"
                    )
                    self.log(msg)
                    self.send_telegram_notify(msg, screenshot)
                    return

                # 6. 点击续期前，先记录当前的到期时间，等下用来验证续期是否真的生效
                before_timestamp = self.get_page_timestamp(sb)
                self.log(f"🕒 续期前到期时间：{before_timestamp}")

                self.log("✅ 检测到可以续期")
                self.click_renew(sb)

                # 给页面切换/弹窗渲染留出时间
                time.sleep(5)

                # 7. 等待可能出现的页面验证
                self.log("⏳ 等待续期后的页面验证完成...")
                self.wait_cloudflare(sb, max_attempts=2, poll_seconds=120)

                # 8. 重新进入面板，并循环确认到期时间/状态已刷新
                self.log("📂 正在重新进入服务器面板确认续期结果...")
                confirmed = False
                final_timestamp = "未找到"

                for attempt in range(1, 4):
                    self.log(f"🔄 第 {attempt}/3 次确认续期结果")
                    self.open_with_retry(
                        sb,
                        URL_APP_PANEL,
                        f"服务器面板确认页 {attempt}",
                        retries=2,
                    )
                    self.human_wait(5, 8)
                    sb.scroll_to_bottom()
                    time.sleep(2)

                    final_timestamp = self.get_page_timestamp(sb)

                    # 真正可靠的验证方式：到期时间是不是真的往后推了。
                    # 之前用"页面上是不是还显示某句固定文案"来判断，
                    # 但那句文案在实际页面里根本不存在，导致永远误判成功。
                    if (
                        final_timestamp != "未找到"
                        and before_timestamp != "未找到"
                        and final_timestamp != before_timestamp
                    ):
                        confirmed = True
                        break

                    self.log(
                        "⚠️ 到期时间暂未变化，"
                        f"续期前：{before_timestamp}，当前：{final_timestamp}，继续等待..."
                    )
                    time.sleep(10)

                final_screenshot = self.safe_screenshot(sb, "final.png")

                if confirmed:
                    msg = (
                        "🎉 WeirdHost-语言版-家宽 续期成功\n"
                        f"🕒 服务器到期时间为：{final_timestamp}"
                    )
                    self.log(msg)
                    self.send_telegram_notify(msg, final_screenshot)
                else:
                    msg = (
                        "⚠️ WeirdHost 已执行续期操作，但到期时间暂未检测到变化\n"
                        f"🕒 续期前到期时间：{before_timestamp}\n"
                        f"🕒 当前检测到的到期时间：{final_timestamp}\n"
                        "请查看截图确认，也可能是页面刷新较慢，实际已续期成功。"
                    )
                    self.log(msg)
                    self.send_telegram_notify(msg, final_screenshot)

            except Exception as e:
                self.log(f"❌ 运行异常: {e}")
                traceback.print_exc()

                error_screenshot = self.safe_screenshot(sb, "error.png")
                self.send_telegram_notify(
                    f"❌ WeirdHost 自动续期运行异常\n{type(e).__name__}: {e}",
                    error_screenshot,
                )


if __name__ == "__main__":
    WeirdHostRenewal().run()
