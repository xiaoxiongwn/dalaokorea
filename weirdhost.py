import time
import os
import json
import re
import random
import traceback

import requests
from seleniumbase import SB


# ================= 环境配置 =================
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

# WeirdHost 服务器 ID，例如：7b471f33
NUM = os.getenv("NUM", "").strip()

# remember_web_... Cookie 的 value
COOKIE = os.getenv("COOKIE", "").strip()

BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"

TIMESTAMP_PATTERN = r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}"


class WeirdHostRenewal:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.base_dir, "artifacts")
        os.makedirs(self.screenshot_dir, exist_ok=True)

    # ============================================================
    # 基础工具
    # ============================================================

    def log(self, msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [INFO] {msg}", flush=True)

    def human_wait(self, min_s=3, max_s=6):
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
                + "。请检查 GitHub Secrets 或 VPS 环境变量。"
            )

    def safe_screenshot(self, sb, filename):
        path = os.path.join(self.screenshot_dir, filename)

        try:
            sb.save_screenshot(path)
            self.log(f"📸 已保存截图：{path}")
            return path
        except Exception as e:
            self.log(f"⚠️ 截图失败：{e}")
            return None

    def send_telegram_notify(self, message, photo_path=None):
        if not TG_TOKEN or not TG_CHAT_ID:
            self.log("⚠️ 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
            return

        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"

                with open(photo_path, "rb") as photo:
                    response = requests.post(
                        url,
                        data={
                            "chat_id": TG_CHAT_ID,
                            "caption": message,
                        },
                        files={"photo": photo},
                        timeout=30,
                    )
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

                response = requests.post(
                    url,
                    data={
                        "chat_id": TG_CHAT_ID,
                        "text": message,
                    },
                    timeout=30,
                )

            response.raise_for_status()
            self.log("✅ Telegram 推送成功")

        except Exception as e:
            self.log(f"⚠️ Telegram 推送失败：{e}")

    def open_with_retry(self, sb, url, description, retries=3):
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                self.log(
                    f"🌐 正在打开{description} "
                    f"（第 {attempt}/{retries} 次）..."
                )

                sb.uc_open_with_reconnect(
                    url,
                    reconnect_time=5,
                )

                time.sleep(4)

                current_url = sb.get_current_url()

                if current_url:
                    self.log(f"✅ {description}已打开：{current_url}")
                    return

            except Exception as e:
                last_error = e
                self.log(f"⚠️ 打开{description}失败：{e}")

            if attempt < retries:
                wait_seconds = attempt * 5
                self.log(f"⏳ {wait_seconds} 秒后重试...")
                time.sleep(wait_seconds)

        raise RuntimeError(
            f"{description}连续 {retries} 次打开失败：{last_error}"
        )

    # ============================================================
    # Cookie 登录
    # ============================================================

    def inject_cookie(self, sb):
        """
        必须先访问 hub.weirdhost.xyz，
        再添加对应域名的 Cookie。
        """

        sb.add_cookie({
            "name": (
                "remember_web_"
                "59ba36addc2b2f9401580f014c7f58ea4e30989d"
            ),
            "value": COOKIE,
            "domain": "hub.weirdhost.xyz",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
            "expires": int(time.time()) + 3600 * 24 * 365,
        })

        self.log("✅ Cookie 注入成功")

    # ============================================================
    # 图片1：点击左侧「서버」
    # ============================================================

    def click_server_menu(self, sb):
        """
        对应图片1红色箭头：
        左侧菜单中的「서버」。
        """

        self.log("🖱️ 步骤1：点击左侧「서버」菜单...")

        selectors = [
            '//a[normalize-space(.)="서버"]',
            '//button[normalize-space(.)="서버"]',
            '//*[self::a or self::button][contains(normalize-space(.), "서버")]',
        ]

        last_error = None

        for selector in selectors:
            try:
                sb.wait_for_element_visible(selector, timeout=15)
                sb.click(selector)
                self.log("✅ 已点击左侧「서버」")
                time.sleep(4)
                return
            except Exception as e:
                last_error = e

        # JS 兜底
        try:
            result = sb.execute_script("""
                const elements = [
                    ...document.querySelectorAll('a, button, [role="button"]')
                ];

                const target = elements.find(el =>
                    el.innerText &&
                    el.innerText.trim() === '서버'
                );

                if (!target) return false;

                target.click();
                return true;
            """)

            if result:
                self.log("✅ 已通过备用方式点击左侧「서버」")
                time.sleep(4)
                return

        except Exception as e:
            last_error = e

        raise RuntimeError(
            f"无法点击左侧「서버」菜单：{last_error}"
        )

    # ============================================================
    # 图片2：点击指定服务器
    # ============================================================

    def click_target_server(self, sb):
        """
        对应图片2红色箭头。

        优先通过 NUM（服务器 ID）定位服务器所在行，
        再点击该行中的可点击元素。

        这样即使服务器名称变化，也不用修改代码。
        """

        self.log(
            f"🖱️ 步骤2：在服务器列表中查找服务器 ID：{NUM}"
        )

        # 先等待服务器 ID 出现在页面中
        id_xpath = (
            f'//*[normalize-space(text())="{NUM}"]'
        )

        try:
            sb.wait_for_element_visible(
                id_xpath,
                timeout=30,
            )
        except Exception as e:
            screenshot = self.safe_screenshot(
                sb,
                "server_list_not_found.png",
            )

            raise RuntimeError(
                f"服务器列表中未找到 NUM={NUM}。"
                f"当前页面截图：{screenshot}"
            ) from e

        # 方法1：
        # 找到 ID 后，点击最近的 table row 内的链接。
        try:
            clicked = sb.execute_script("""
                const serverId = arguments[0];

                const all = [
                    ...document.querySelectorAll(
                        'td, div, span, p'
                    )
                ];

                const idElement = all.find(el =>
                    el.textContent.trim() === serverId
                );

                if (!idElement) {
                    return "SERVER_ID_NOT_FOUND";
                }

                // 找到服务器所在的行
                const row =
                    idElement.closest('tr') ||
                    idElement.parentElement;

                if (row) {
                    const clickable =
                        row.querySelector(
                            'a, button, [role="button"]'
                        );

                    if (clickable) {
                        clickable.click();
                        return "ROW_CLICKABLE";
                    }

                    // 如果整行本身绑定点击事件，
                    // 直接点击服务器 ID 元素。
                    idElement.click();
                    return "ID_CLICK";
                }

                idElement.click();
                return "ID_CLICK";
            """, NUM)

            if clicked in ("ROW_CLICKABLE", "ID_CLICK"):
                self.log(
                    f"✅ 已点击服务器 ID：{NUM} "
                    f"（{clicked}）"
                )
                time.sleep(5)
                return

        except Exception as e:
            self.log(f"⚠️ 表格定位点击失败：{e}")

        # 方法2：直接点击 ID
        try:
            sb.click(id_xpath)
            self.log(f"✅ 已直接点击服务器 ID：{NUM}")
            time.sleep(5)
            return
        except Exception as e:
            raise RuntimeError(
                f"无法点击服务器 {NUM}：{e}"
            )

    # ============================================================
    # 图片3：读取时间和续期按钮状态
    # ============================================================

    def get_expiry_time(self, sb):
        """
        读取图片3黄色箭头所指的时间，例如：
        유통기한 2026-09-05 21:09:46
        """

        try:
            page_text = sb.get_text("body")

            # 优先寻找「유통기한」附近的时间
            expiry_match = re.search(
                r"유통기한\s*(" + TIMESTAMP_PATTERN + r")",
                page_text,
                re.IGNORECASE,
            )

            if expiry_match:
                return expiry_match.group(1)

            # 兜底：寻找页面第一个完整时间
            timestamp_match = re.search(
                TIMESTAMP_PATTERN,
                page_text,
            )

            if timestamp_match:
                return timestamp_match.group(0)

        except Exception as e:
            self.log(f"⚠️ 读取到期时间失败：{e}")

        return None

    def get_renew_status_text(self, sb):
        """
        获取图片3时间下方的提示文字，例如：
        4일 후에 연장할 수 있어요
        或
        지금 연장이 가능해요
        """

        try:
            page_text = sb.get_text("body")

            lines = [
                line.strip()
                for line in page_text.splitlines()
                if line.strip()
            ]

            status_lines = []

            for line in lines:
                if (
                    "연장" in line
                    or "후에" in line
                    or "지금" in line
                ):
                    status_lines.append(line)

            return " | ".join(status_lines[-5:])

        except Exception as e:
            self.log(f"⚠️ 读取续期状态失败：{e}")
            return ""

    def is_renew_button_enabled(self, sb):
        """
        判断图片3红色箭头「연장하기」按钮是否真正可点击。

        判断顺序：
        1. 页面出现「지금 연장이 가능해요」
        2. 检查 연장하기 按钮 disabled 属性
        """

        page_text = sb.get_text("body")

        # 网站明确提示现在可以续期
        if "지금 연장이 가능해요" in page_text:
            return True

        try:
            result = sb.execute_script("""
                const buttons = [
                    ...document.querySelectorAll('button')
                ];

                const button = buttons.find(btn =>
                    btn.innerText &&
                    btn.innerText.trim().includes('연장하기')
                );

                if (!button) {
                    return {
                        found: false,
                        disabled: true,
                    };
                }

                const style = window.getComputedStyle(button);

                const disabled =
                    button.disabled ||
                    button.getAttribute('aria-disabled') === 'true' ||
                    style.pointerEvents === 'none';

                return {
                    found: true,
                    disabled: disabled,
                };
            """)

            if result and result.get("found"):
                return not result.get("disabled")

        except Exception as e:
            self.log(f"⚠️ 检查续期按钮状态失败：{e}")

        return False

    def click_renew_button(self, sb):
        """
        对应图片3红色箭头：
        点击「연장하기」。
        """

        self.log("🖱️ 步骤3：点击「연장하기」按钮...")

        xpath = (
            '//button[contains(normalize-space(.), "연장하기")]'
        )

        try:
            sb.wait_for_element_visible(
                xpath,
                timeout=20,
            )

            sb.scroll_to(xpath)
            time.sleep(1)

            sb.click(xpath)

            self.log("🎉 已点击「연장하기」")
            return

        except Exception as first_error:
            self.log(
                f"⚠️ 普通点击失败，尝试 JS 点击："
                f"{first_error}"
            )

        result = sb.execute_script("""
            const buttons = [
                ...document.querySelectorAll('button')
            ];

            const button = buttons.find(btn =>
                btn.innerText &&
                btn.innerText.trim().includes('연장하기')
            );

            if (!button) {
                return false;
            }

            if (button.disabled) {
                return false;
            }

            button.click();
            return true;
        """)

        if result:
            self.log("🎉 已通过备用方式点击「연장하기」")
            return

        raise RuntimeError(
            "「연장하기」按钮无法点击"
        )

    # ============================================================
    # 续期流程
    # ============================================================

    def run(self):
        self.log("=" * 55)
        self.log("🚀 WeirdHost 自动续期任务开始")
        self.log("=" * 55)

        self.validate_config()

        self.log(f"🎯 目标服务器 ID：{NUM}")

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
                self.log("✅ Chrome 浏览器已启动")

                # ------------------------------------------------
                # 1. 打开登录页
                # ------------------------------------------------
                self.open_with_retry(
                    sb,
                    LOGIN_URL,
                    "WeirdHost 登录页面",
                )

                time.sleep(5)

                # ------------------------------------------------
                # 2. 注入 Cookie
                # ------------------------------------------------
                self.inject_cookie(sb)

                # 刷新首页，使 Cookie 生效
                self.open_with_retry(
                    sb,
                    BASE_URL,
                    "WeirdHost 首页",
                )

                self.human_wait(3, 5)

                # ------------------------------------------------
                # 图片1
                # 点击左侧「서버」
                # ------------------------------------------------
                self.click_server_menu(sb)

                # ------------------------------------------------
                # 图片2
                # 点击目标服务器
                # ------------------------------------------------
                self.click_target_server(sb)

                # 等待详情页加载
                self.human_wait(4, 7)

                # 滚动到底部，图片3中的续期区域通常位于下方
                self.log("📜 正在滚动到续期区域...")
                sb.scroll_to_bottom()
                time.sleep(3)

                # ------------------------------------------------
                # 图片3
                # 读取黄色箭头所指的时间
                # ------------------------------------------------
                expiry_time = self.get_expiry_time(sb)

                if expiry_time:
                    self.log(
                        f"🕒 当前服务器到期时间：{expiry_time}"
                    )
                else:
                    self.log(
                        "⚠️ 未能读取到期时间"
                    )

                renew_status = self.get_renew_status_text(sb)

                if renew_status:
                    self.log(
                        f"📋 当前续期状态：{renew_status}"
                    )

                # ------------------------------------------------
                # 判断是否达到续期时间
                # ------------------------------------------------
                renew_available = (
                    self.is_renew_button_enabled(sb)
                )

                if not renew_available:
                    message = "⏳ 时间尚未达到"

                    if expiry_time:
                        message += (
                            f"\n🕒 服务器到期时间："
                            f"{expiry_time}"
                        )

                    if renew_status:
                        message += (
                            f"\n📋 页面提示："
                            f"{renew_status}"
                        )

                    self.log(message)

                    screenshot = self.safe_screenshot(
                        sb,
                        "time_not_reached.png",
                    )

                    self.send_telegram_notify(
                        message,
                        screenshot,
                    )

                    return

                # ------------------------------------------------
                # 达到续期时间
                # 图片3点击红色箭头按钮
                # ------------------------------------------------
                self.log(
                    "🎉 已达到续期时间，"
                    "续期按钮可以点击"
                )

                before_expiry = expiry_time

                self.click_renew_button(sb)

                # 等待服务器续期请求完成
                time.sleep(10)

                # ------------------------------------------------
                # 再次检查结果
                # ------------------------------------------------
                sb.refresh()
                time.sleep(8)

                sb.scroll_to_bottom()
                time.sleep(3)

                after_expiry = self.get_expiry_time(sb)
                after_status = self.get_renew_status_text(sb)

                screenshot = self.safe_screenshot(
                    sb,
                    "renew_result.png",
                )

                # 页面时间发生变化通常表示续期成功
                if (
                    after_expiry
                    and after_expiry != before_expiry
                ):
                    message = (
                        "🎉 WeirdHost 续期成功\n"
                        f"🕒 新的服务器到期时间："
                        f"{after_expiry}"
                    )
                else:
                    message = (
                        "⚠️ 已点击续期按钮，"
                        "请检查页面确认最终结果"
                    )

                    if after_expiry:
                        message += (
                            f"\n🕒 当前到期时间："
                            f"{after_expiry}"
                        )

                    if after_status:
                        message += (
                            f"\n📋 页面状态："
                            f"{after_status}"
                        )

                self.log(message)

                self.send_telegram_notify(
                    message,
                    screenshot,
                )

            except Exception as e:
                self.log(f"❌ 运行异常：{e}")

                traceback.print_exc()

                error_screenshot = self.safe_screenshot(
                    sb,
                    "error.png",
                )

                self.send_telegram_notify(
                    (
                        "❌ WeirdHost 自动续期运行异常\n"
                        f"{type(e).__name__}: {e}"
                    ),
                    error_screenshot,
                )


if __name__ == "__main__":
    WeirdHostRenewal().run()
