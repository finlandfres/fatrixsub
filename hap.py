import telebot
from telebot import types
from flask import Flask, Response, request
import threading
import requests
import time
import os
import re
import base64
import json
import yaml

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8221100964:AAFCPZXJ8bVCPWhOF0GBdMKeSy8jHUdB_XE")
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.environ.get("PORT", 5000))
BASE_URL = os.environ.get("BASE_URL", f"https://fatrixsub.onrender.com")

TRAFFIC_LIMIT_MB = 500
RENEW_THRESHOLD_MB = 200
CHECK_INTERVAL = 60
# ===================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
user_data = {}


# ==================== HAPP API ====================

def fetch_sub_url_from_happ():
    """
    POST /api/free-trial → получаем subscriptionUrl напрямую.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://happ.business",
        "Referer": "https://happ.business/access/key",
    })

    try:
        print("[Happ] Запрашиваем free-trial...")
        resp = session.post(
            "https://happ.business/api/free-trial",
            json={},
            timeout=15
        )
        data = resp.json()
        print(f"[Happ] Ответ: {data}")

        if not data.get("ok"):
            print(f"[Happ] Сервер вернул ошибку: {data}")
            return None

        sub_url = data.get("subscriptionUrl")
        if not sub_url:
            print("[Happ] subscriptionUrl отсутствует в ответе")
            return None

        print(f"[Happ] subscriptionUrl: {sub_url}")
        return sub_url

    except Exception as e:
        print(f"[Happ] Исключение: {e}")
        return None


# ==================== ПАРСИНГ ПОДПИСКИ ====================

def extract_all_vless_from_subscription(sub_url):
    """
    Скачивает подписку remnawave (YAML/base64/plain)
    и возвращает СПИСОК всех vless:// URI.
    """
    try:
        headers = {
            "User-Agent": "clash-verge/1.6.1",
            "Accept": "text/yaml, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
        }
        resp = requests.get(sub_url, headers=headers, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        content = resp.text.strip()

        print(f"[Parser] Status: {resp.status_code}")
        print(f"[Parser] Content-Type: {content_type}")
        print(f"[Parser] Длина контента: {len(content)} символов")
        print(f"[Parser] Первые 200 символов:\n{content[:200]}")

        vless_list = []

        # ── Путь 1: YAML (Clash конфиг) ──────────────────────────
        if (
            "yaml" in content_type
            or content.lstrip().startswith("proxies:")
            or "proxies:" in content
            or "proxy-groups:" in content
        ):
            print("[Parser] Определён формат: YAML (Clash)")
            vless_list = _parse_yaml_all(content)
            if vless_list:
                return vless_list

        # ── Путь 2: Base64 ────────────────────────────────────────
        try:
            padded = content + "=" * (4 - len(content) % 4)
            decoded = base64.b64decode(padded).decode("utf-8")
            print("[Parser] Декодировано из base64")

            if "proxies:" in decoded:
                print("[Parser] Base64 содержит YAML")
                vless_list = _parse_yaml_all(decoded)
                if vless_list:
                    return vless_list

            # Plain vless строки
            for line in decoded.splitlines():
                line = line.strip()
                if line.startswith("vless://"):
                    vless_list.append(line)

            if vless_list:
                print(f"[Parser] Из base64 plain: {len(vless_list)} vless")
                return vless_list

        except Exception:
            pass

        # ── Путь 3: Plain text vless строки ──────────────────────
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("vless://"):
                vless_list.append(line)

        if vless_list:
            print(f"[Parser] Plain text: {len(vless_list)} vless")
            return vless_list

        # ── Путь 4: Regex по всему тексту ────────────────────────
        found = re.findall(r'vless://[^\s\n"\'<>]+', content)
        if found:
            print(f"[Parser] Regex нашёл: {len(found)} vless")
            return found

        print("[Parser] vless не найден ни одним методом")
        return []

    except Exception as e:
        print(f"[Parser] Критическая ошибка: {e}")
        return []


def _parse_yaml_all(yaml_content):
    """
    Парсит Clash YAML и конвертирует ВСЕ прокси в vless:// URI.
    """
    try:
        config = yaml.safe_load(yaml_content)
        if not config:
            print("[YAML] Пустой конфиг после парсинга")
            return []

        proxies = config.get("proxies", [])
        print(f"[YAML] Всего прокси в конфиге: {len(proxies)}")

        vless_list = []

        for proxy in proxies:
            ptype = str(proxy.get("type", "")).lower()
            name = proxy.get("name", "")
            print(f"[YAML] Прокси: type={ptype} name={name}")

            if ptype == "vless":
                uri = _clash_vless_to_uri(proxy)
                if uri:
                    vless_list.append(uri)
                    print(f"[YAML] ✓ Добавлен: {uri[:80]}...")
                else:
                    print(f"[YAML] ✗ Не удалось конвертировать: {proxy}")

            # Если когда-нибудь добавят другие типы — расширим здесь
            else:
                print(f"[YAML] Пропущен тип: {ptype}")

        print(f"[YAML] Итого vless URI: {len(vless_list)}")
        return vless_list

    except yaml.YAMLError as e:
        print(f"[YAML] Ошибка парсинга YAML: {e}")
        return []
    except Exception as e:
        print(f"[YAML] Исключение: {e}")
        return []


def _clash_vless_to_uri(proxy):
    """
    Конвертирует Clash YAML vless объект → vless:// URI строку.

    Поддерживает:
      - TLS / Reality / без шифрования
      - TCP / WS / gRPC / HTTPUpgrade транспорт
      - flow (xtls-rprx-vision)
      - client-fingerprint
    """
    try:
        uuid       = proxy.get("uuid", "")
        server     = proxy.get("server", "")
        port       = proxy.get("port", 443)
        name       = proxy.get("name", "FatrixVPN")
        network    = str(proxy.get("network", "tcp")).lower()
        tls        = proxy.get("tls", False)
        servername = proxy.get("servername") or proxy.get("sni", "")
        flow       = proxy.get("flow", "")
        fingerprint = proxy.get("client-fingerprint", "")

        reality_opts    = proxy.get("reality-opts", {}) or {}
        ws_opts         = proxy.get("ws-opts", {}) or {}
        grpc_opts       = proxy.get("grpc-opts", {}) or {}
        http_opts       = proxy.get("http-opts", {}) or {}
        httpupgrade_opts = proxy.get("httpupgrade-opts", {}) or {}

        if not uuid or not server:
            print(f"[URI] Пропуск — нет uuid/server: {proxy}")
            return None

        params = {}

        # ── Security ─────────────────────────────────────────────
        if reality_opts:
            params["security"] = "reality"
            pub_key   = reality_opts.get("public-key", "")
            short_id  = reality_opts.get("short-id", "")
            if pub_key:
                params["pbk"] = pub_key
            if short_id:
                params["sid"] = short_id
        elif tls:
            params["security"] = "tls"
        else:
            params["security"] = "none"

        # ── SNI ──────────────────────────────────────────────────
        if servername:
            params["sni"] = servername

        # ── Fingerprint ──────────────────────────────────────────
        if fingerprint:
            params["fp"] = fingerprint

        # ── Flow ─────────────────────────────────────────────────
        if flow:
            params["flow"] = flow

        # ── Transport / Network ──────────────────────────────────
        params["type"] = network

        if network == "ws":
            path = ws_opts.get("path", "/")
            host = (ws_opts.get("headers") or {}).get("Host", "")
            params["path"] = path
            if host:
                params["host"] = host

        elif network == "grpc":
            svc = grpc_opts.get("grpc-service-name", "")
            if svc:
                params["serviceName"] = svc

        elif network == "http":
            path_list = http_opts.get("path", ["/"])
            path = path_list[0] if isinstance(path_list, list) else path_list
            host_list = http_opts.get("headers", {}).get("Host", [""])
            host = host_list[0] if isinstance(host_list, list) else host_list
            params["path"] = path
            if host:
                params["host"] = host

        elif network == "httpupgrade":
            path = httpupgrade_opts.get("path", "/")
            host = httpupgrade_opts.get("host", "")
            params["path"] = path
            if host:
                params["host"] = host

        # ── Собираем URI ─────────────────────────────────────────
        def encode(v):
            return requests.utils.quote(str(v), safe="")

        query = "&".join(
            f"{k}={encode(v)}"
            for k, v in params.items()
            if str(v) != ""
        )

        fragment = requests.utils.quote(name, safe="")
        uri = f"vless://{uuid}@{server}:{port}?{query}#{fragment}"
        return uri

    except Exception as e:
        print(f"[URI] Исключение при конвертации: {e}")
        return None


# ==================== ТРАФИК ====================

def get_used_traffic_mb(sub_url):
    """Получает трафик из заголовка Subscription-Userinfo."""
    try:
        resp = requests.get(
            sub_url,
            headers={"User-Agent": "clash-verge/1.6.1"},
            timeout=15
        )
        # Заголовки регистронезависимы
        userinfo = (
            resp.headers.get("subscription-userinfo")
            or resp.headers.get("Subscription-Userinfo")
            or ""
        )
        print(f"[Traffic] Userinfo: {userinfo}")

        if not userinfo:
            return 0.0

        upload   = int(re.search(r"upload=(\d+)",   userinfo).group(1)) \
            if re.search(r"upload=(\d+)", userinfo)   else 0
        download = int(re.search(r"download=(\d+)", userinfo).group(1)) \
            if re.search(r"download=(\d+)", userinfo) else 0

        used_mb = (upload + download) / (1024 * 1024)
        print(f"[Traffic] Использовано: {used_mb:.2f} MB")
        return used_mb

    except Exception as e:
        print(f"[Traffic] Ошибка: {e}")
        return 0.0


# ==================== FLASK ====================

def build_sub_content(vless_list):
    """
    Формирует base64 подписку из списка vless URI.
    Комментарии внутри base64 — можно на русском.
    """
    lines = [
        "# FatrixVPN",
        "# Когда заканчивается лимит обнови подписку!",
    ] + vless_list

    content = "\n".join(lines)
    return base64.b64encode(content.encode("utf-8")).decode("utf-8")


@app.route("/")
def index():
    return Response("FatrixVPN is running!", content_type="text/plain")


@app.route("/health")
def health():
    return Response("OK", content_type="text/plain")


@app.route("/sub/<int:user_id>")
def serve_subscription(user_id):
    """Репрокси подписки — отдаёт все vless пользователя."""
    data = user_data.get(user_id)

    if not data or not data.get("vless_list"):
        return Response(
            "Key not found. Request it via Telegram bot.",
            status=404,
            content_type="text/plain"
        )

    used_mb = data.get("used_mb", 0)

    # Только ASCII в заголовках!
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Subscription-Userinfo": (
            f"upload=0; "
            f"download={int(used_mb * 1024 * 1024)}; "
            f"total={TRAFFIC_LIMIT_MB * 1024 * 1024}; "
            f"expire=0"
        ),
        "Profile-Title": "FatrixVPN",
        "Profile-Update-Interval": "1",
        "Support-URL": f"{BASE_URL}/sub/{user_id}",
        "Content-Disposition": "attachment; filename=FatrixVPN.txt",
    }

    return Response(
        build_sub_content(data["vless_list"]),
        headers=headers
    )


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        update = telebot.types.Update.de_json(
            request.get_data().decode("utf-8")
        )
        bot.process_new_updates([update])
        return Response("OK", status=200)
    return Response(status=403)


# ==================== МОНИТОРИНГ ====================

def monitor_traffic():
    print("[Monitor] Запущен.")
    while True:
        time.sleep(CHECK_INTERVAL)

        for uid, data in list(user_data.items()):
            sub_url = data.get("sub_url")
            if not sub_url or data.get("renewing"):
                continue

            used_mb = get_used_traffic_mb(sub_url)
            user_data[uid]["used_mb"] = used_mb
            remaining_mb = TRAFFIC_LIMIT_MB - used_mb

            print(f"[Monitor] uid={uid}: {used_mb:.1f}/{TRAFFIC_LIMIT_MB} MB")

            # Лимит исчерпан
            if used_mb >= TRAFFIC_LIMIT_MB and not data.get("warned"):
                user_data[uid]["warned"] = True
                try:
                    bot.send_message(
                        uid,
                        "⚠️ *Ваш трафик закончился!*\n\n"
                        "Нажмите кнопку ниже для обновления:",
                        parse_mode="Markdown",
                        reply_markup=get_renew_keyboard()
                    )
                except Exception as e:
                    print(f"[Monitor] Ошибка уведомления {uid}: {e}")

            # Мало осталось — автообновление
            elif 0 < remaining_mb <= RENEW_THRESHOLD_MB \
                    and not data.get("auto_renewed"):
                user_data[uid]["auto_renewed"] = True
                try:
                    bot.send_message(
                        uid,
                        f"🔄 Осталось менее *{RENEW_THRESHOLD_MB} МБ*.\n"
                        "Автоматически обновляю ключ...",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                threading.Thread(
                    target=auto_renew_key,
                    args=(uid,),
                    daemon=True
                ).start()


def auto_renew_key(user_id):
    user_data[user_id]["renewing"] = True

    new_sub = fetch_sub_url_from_happ()
    if not new_sub:
        user_data[user_id]["renewing"] = False
        try:
            bot.send_message(
                user_id,
                "❌ Не удалось обновить ключ. Попробуйте /renew"
            )
        except Exception:
            pass
        return

    new_vless_list = extract_all_vless_from_subscription(new_sub)
    if not new_vless_list:
        user_data[user_id]["renewing"] = False
        try:
            bot.send_message(
                user_id,
                "❌ Не удалось извлечь vless. Попробуйте /renew"
            )
        except Exception:
            pass
        return

    user_data[user_id].update({
        "sub_url":    new_sub,
        "vless_list": new_vless_list,
        "used_mb":    0,
        "warned":     False,
        "auto_renewed": False,
        "renewing":   False,
    })

    proxy_url = f"{BASE_URL}/sub/{user_id}"
    try:
        bot.send_message(
            user_id,
            f"✅ *Ключ автоматически обновлён!*\n\n"
            f"📡 *Подписка:*\n`{proxy_url}`\n\n"
            f"🔢 Серверов: *{len(new_vless_list)}*\n\n"
            "Обновите подписку в приложении.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        print(f"[AutoRenew] Ошибка: {e}")


# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "🔑 Получить ключ",      callback_data="get_key"),
        types.InlineKeyboardButton(
            "📊 Мой трафик",         callback_data="my_traffic"),
        types.InlineKeyboardButton(
            "🔄 Обновить подписку",  callback_data="renew_key"),
    )
    return markup


def get_renew_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "🔄 Обновить подписку", callback_data="renew_key")
    )
    return markup


# ==================== КОМАНДЫ ====================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "друг"
    bot.send_message(
        uid,
        f"👋 Привет, *{name}*!\n\n"
        "🌐 *FatrixVPN* — быстрый и стабильный VPN.\n\n"
        f"📌 Лимит трафика: *{TRAFFIC_LIMIT_MB} МБ*\n"
        "⚠️ Когда заканчивается лимит — обнови подписку!\n\n"
        "Нажми *«Получить ключ»* чтобы начать:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


@bot.message_handler(commands=["key"])
def cmd_key(message):
    handle_get_key(message.from_user.id)


@bot.message_handler(commands=["traffic"])
def cmd_traffic(message):
    handle_traffic_check(message.from_user.id)


@bot.message_handler(commands=["renew"])
def cmd_renew(message):
    handle_renew_key(message.from_user.id)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(
        message.from_user.id,
        "📖 *Команды FatrixVPN:*\n\n"
        "/start — Главное меню\n"
        "/key — Получить ключ\n"
        "/traffic — Статистика трафика\n"
        "/renew — Обновить ключ\n"
        "/help — Помощь",
        parse_mode="Markdown"
    )


# ==================== CALLBACKS ====================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    if call.data == "get_key":
        handle_get_key(uid)
    elif call.data == "my_traffic":
        handle_traffic_check(uid)
    elif call.data == "renew_key":
        handle_renew_key(uid)


# ==================== ЛОГИКА ====================

def handle_get_key(user_id):
    data = user_data.get(user_id)

    if data and data.get("vless_list"):
        proxy_url   = f"{BASE_URL}/sub/{user_id}"
        used_mb     = data.get("used_mb", 0)
        remaining   = max(0, TRAFFIC_LIMIT_MB - used_mb)
        count       = len(data["vless_list"])
        bot.send_message(
            user_id,
            "✅ *У вас уже есть активный ключ!*\n\n"
            f"📡 *Ваша подписка:*\n`{proxy_url}`\n\n"
            f"🔢 Серверов: *{count}*\n"
            f"📊 Использовано: *{used_mb:.1f}* из *{TRAFFIC_LIMIT_MB} МБ*\n"
            f"⚡ Осталось: *{remaining:.1f} МБ*\n\n"
            "⚠️ Когда заканчивается лимит — обнови подписку!",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    msg = bot.send_message(
        user_id,
        "⏳ *Получаем ваш ключ...*\n\n`[░░░░░░░░░░] 0%`",
        parse_mode="Markdown"
    )
    threading.Thread(
        target=_fetch_and_send_key,
        args=(user_id, msg.message_id),
        daemon=True
    ).start()


def _fetch_and_send_key(user_id, msg_id):
    def edit(text):
        try:
            bot.edit_message_text(
                text, user_id, msg_id, parse_mode="Markdown"
            )
        except Exception:
            pass

    edit("🔍 *Запрашиваем ключ...*\n\n`[███░░░░░░░] 30%`")

    sub_url = fetch_sub_url_from_happ()
    if not sub_url:
        edit("❌ *Не удалось получить ключ.*\n\nПопробуйте позже: /key")
        return

    edit("📦 *Загружаем серверы...*\n\n`[███████░░░] 70%`")

    vless_list = extract_all_vless_from_subscription(sub_url)
    if not vless_list:
        edit("❌ *Не удалось извлечь серверы.*\n\nПопробуйте: /key")
        return

    user_data[user_id] = {
        "sub_url":    sub_url,
        "vless_list": vless_list,
        "used_mb":    0,
        "warned":     False,
        "auto_renewed": False,
        "renewing":   False,
    }

    proxy_url = f"{BASE_URL}/sub/{user_id}"
    edit("✅ *Готово!*\n\n`[██████████] 100%`")
    time.sleep(0.8)

    try:
        bot.send_message(
            user_id,
            f"🎉 *Ваш FatrixVPN ключ готов!*\n\n"
            f"📡 *Ссылка на подписку:*\n`{proxy_url}`\n\n"
            f"🔢 Серверов в подписке: *{len(vless_list)}*\n"
            f"📊 Лимит: *{TRAFFIC_LIMIT_MB} МБ*\n"
            "⚠️ Когда заканчивается лимит — обнови подписку!\n\n"
            "📱 *Как добавить:*\n"
            "Скопируй ссылку → вставь в приложение → "
            "Добавить подписку",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        print(f"[Bot] Ошибка отправки: {e}")


def handle_traffic_check(user_id):
    data = user_data.get(user_id)
    if not data or not data.get("sub_url"):
        bot.send_message(
            user_id,
            "❌ У вас нет активного ключа.\n\n"
            "Нажмите *«Получить ключ»*:",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    fresh = get_used_traffic_mb(data["sub_url"])
    user_data[user_id]["used_mb"] = fresh

    used_mb     = fresh
    remaining   = max(0, TRAFFIC_LIMIT_MB - used_mb)
    percent     = min(100, (used_mb / TRAFFIC_LIMIT_MB) * 100)
    filled      = int(percent / 10)
    bar         = "█" * filled + "░" * (10 - filled)
    count       = len(data.get("vless_list", []))

    if used_mb >= TRAFFIC_LIMIT_MB:
        status = "❌ Лимит исчерпан"
    elif remaining <= RENEW_THRESHOLD_MB:
        status = "⚠️ Почти закончился"
    else:
        status = "✅ Активен"

    bot.send_message(
        user_id,
        f"📊 *Статистика FatrixVPN*\n\n"
        f"Статус: {status}\n"
        f"`[{bar}] {percent:.0f}%`\n\n"
        f"📤 Использовано: *{used_mb:.1f} МБ*\n"
        f"📥 Осталось: *{remaining:.1f} МБ*\n"
        f"📦 Лимит: *{TRAFFIC_LIMIT_MB} МБ*\n"
        f"🔢 Серверов: *{count}*",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


def handle_renew_key(user_id):
    if user_data.get(user_id, {}).get("renewing"):
        bot.send_message(user_id, "⏳ Уже обновляем, подождите...")
        return

    if user_id not in user_data:
        user_data[user_id] = {}

    msg = bot.send_message(
        user_id,
        "🔄 *Обновляем ваш ключ...*\n\n`[░░░░░░░░░░] 0%`",
        parse_mode="Markdown"
    )
    threading.Thread(
        target=_renew_and_send,
        args=(user_id, msg.message_id),
        daemon=True
    ).start()


def _renew_and_send(user_id, msg_id):
    def edit(text, markup=None):
        try:
            bot.edit_message_text(
                text, user_id, msg_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
        except Exception:
            pass

    user_data[user_id]["renewing"] = True
    edit("🔍 *Получаем новый ключ...*\n\n`[████░░░░░░] 40%`")

    new_sub = fetch_sub_url_from_happ()
    if not new_sub:
        user_data[user_id]["renewing"] = False
        edit("❌ *Не удалось получить ключ.*\n\nПопробуйте /renew снова.")
        return

    edit("📦 *Загружаем серверы...*\n\n`[████████░░] 80%`")

    new_vless_list = extract_all_vless_from_subscription(new_sub)
    if not new_vless_list:
        user_data[user_id]["renewing"] = False
        edit("❌ *Не удалось извлечь серверы.*\n\nПопробуйте /renew снова.")
        return

    user_data[user_id].update({
        "sub_url":    new_sub,
        "vless_list": new_vless_list,
        "used_mb":    0,
        "warned":     False,
        "auto_renewed": False,
        "renewing":   False,
    })

    proxy_url = f"{BASE_URL}/sub/{user_id}"
    edit(
        f"✅ *Ключ обновлён!*\n\n"
        f"📡 *Новая подписка:*\n`{proxy_url}`\n\n"
        f"🔢 Серверов: *{len(new_vless_list)}*\n"
        f"📊 Лимит сброшен: *0 / {TRAFFIC_LIMIT_MB} МБ*\n"
        "⚠️ Когда заканчивается лимит — обнови подписку!\n\n"
        "Обновите подписку в приложении.",
        markup=get_main_keyboard()
    )


# ==================== ЗАПУСК ====================

def setup_webhook():
    webhook_url = f"{BASE_URL}/webhook/{BOT_TOKEN}"
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        print(f"[Webhook] Установлен: {webhook_url}")
    except Exception as e:
        print(f"[Webhook] Ошибка: {e}")


if __name__ == "__main__":
    print("=" * 45)
    print("       FatrixVPN Bot")
    print("=" * 45)

    is_render = bool(os.environ.get("RENDER"))

    monitor_thread = threading.Thread(target=monitor_traffic, daemon=True)
    monitor_thread.start()
    print("[Monitor] Запущен.")

    if is_render:
        print("[Mode] Render → Webhook")
        setup_webhook()
        app.run(host=FLASK_HOST, port=FLASK_PORT)
    else:
        print("[Mode] Localhost → Polling")
        flask_thread = threading.Thread(
            target=lambda: app.run(
                host="127.0.0.1", port=FLASK_PORT, debug=False
            ),
            daemon=True
        )
        flask_thread.start()
        print(f"[Flask] http://127.0.0.1:{FLASK_PORT}")
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
