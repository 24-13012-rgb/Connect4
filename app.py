from flask import Flask, render_template, session, jsonify, request, redirect, url_for, Response
import sqlite3
import os
import re
import calendar
import random
import string
import time
import json
import io
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
import qrcode
import qrcode.image.svg

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "connect4-booth-secret")  # override via env var when deployed publicly

# When deployed behind a hosting platform's reverse proxy (Render, Railway,
# etc.), the proxy terminates HTTPS and forwards plain HTTP internally. This
# tells Flask to trust the proxy's X-Forwarded-* headers so url_for(...,
# _external=True) builds correct https:// join links instead of http://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Password that unlocks the records/leaderboard page (set ADMIN_PASSWORD in
# your hosting platform's Environment settings; this fallback is only for
# local testing).
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "connect4admin")

ROWS = 6
COLS = 7
DB_PATH = os.path.join(os.path.dirname(__file__), "records.db")

# Records normally live in a local SQLite file, which works great except for
# one thing: on free hosting (Render, etc.) that file lives on ephemeral
# storage and gets wiped every time the app is redeployed. Setting a
# DATABASE_URL environment variable (e.g. a free Postgres database from
# Neon/Supabase) switches records over to that instead, so they survive
# redeploys indefinitely. Nothing else about the app changes either way.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


class _PostgresConn:
    """Thin wrapper so the rest of the file can keep using the same
    sqlite3-style calling convention (conn.execute(...).fetchall(), etc.)
    regardless of which database is actually in use."""

    def __init__(self, dsn):
        self._conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

# ---------- Shared game rooms ----------
# Games used to live entirely in the per-browser session, which meant two
# different devices could never actually play each other -- each one just
# got its own private, empty game. Now a game lives here, in memory, keyed
# by a short room code that both devices share. Each browser's session just
# remembers which room it's in and whether it's Player 1 or Player 2.
GAMES = {}

# Characters chosen to avoid look-alikes (no 0/O, 1/I/L) so a code is easy
# to read aloud or type on a phone.
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_room_code():
    for _ in range(50):
        code = "".join(random.choices(ROOM_CODE_ALPHABET, k=5))
        if code not in GAMES:
            return code
    raise RuntimeError("Could not generate a unique room code")


def create_game(player1_name, player1_color):
    code = generate_room_code()
    GAMES[code] = {
        "board": new_board(),
        "turn": 1,
        "winner": 0,
        "scores": {"1": 0, "2": 0},
        "player1_name": player1_name,
        "player1_color": player1_color,
        "player2_name": None,
        "player2_color": None,
        "player2_joined": False,
        "last_move": None,
        "winning_cells": None,
        "created_at": time.time(),
    }
    return code


def get_game(room_id):
    return GAMES.get(room_id)


def game_state_json(game):
    color1 = game["player1_color"]
    color2 = game["player2_color"] or DEFAULT_COLOR_2
    last_move = game.get("last_move") or {}
    return {
        "board": game["board"],
        "turn": game["turn"],
        "winner": game["winner"],
        "scores": game["scores"],
        "player1_name": game["player1_name"],
        "player2_name": game["player2_name"],
        "player1_color": color1,
        "player2_color": color2,
        "player1_color_light": shade_hex(color1, 60),
        "player2_color_light": shade_hex(color2, 60),
        "player2_joined": game["player2_joined"],
        "row": last_move.get("row"),
        "col": last_move.get("col"),
        "move_id": last_move.get("move_id", 0),
        "winning_cells": game.get("winning_cells"),
    }

# Bump this any time static/script.js or static/style.css change.
# It's appended as a query string on those files, which forces browsers
# to fetch the new version instead of serving a stale cached copy.
ASSET_VERSION = "12"

DEFAULT_COLOR_1 = "#ef4444"  # red
DEFAULT_COLOR_2 = "#eab308"  # yellow

# Fixed, friendly palette so players pick a color instead of using a raw
# color-wheel picker. Whoever joins second has any color already taken by
# the other player disabled (see join_game()).
COLOR_PALETTE = [
    {"hex": "#ef4444", "name": "Red"},
    {"hex": "#f97316", "name": "Orange"},
    {"hex": "#eab308", "name": "Yellow"},
    {"hex": "#22c55e", "name": "Green"},
    {"hex": "#14b8a6", "name": "Teal"},
    {"hex": "#3b82f6", "name": "Blue"},
    {"hex": "#6366f1", "name": "Indigo"},
    {"hex": "#a855f7", "name": "Purple"},
    {"hex": "#ec4899", "name": "Pink"},
    {"hex": "#64748b", "name": "Gray"},
]
PALETTE_HEXES = {c["hex"].lower() for c in COLOR_PALETTE}


@app.context_processor
def inject_asset_version():
    return {"asset_version": ASSET_VERSION}


def is_valid_hex_color(value):
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""))


def generate_qr_svg(data):
    """Renders a scannable QR code for `data` as a small, self-contained
    inline SVG string (no external image file, no JS library) with a white
    background added for reliable scanning against the page's background."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    raw = buf.getvalue().decode("utf-8")

    viewbox_match = re.search(r'viewBox="([^"]+)"', raw)
    viewbox = viewbox_match.group(1) if viewbox_match else "0 0 33 33"
    size = viewbox.split()[-1]

    path_match = re.search(r"(<path .*?/>)", raw, re.S)
    path = path_match.group(1) if path_match else ""

    return (
        f'<svg viewBox="{viewbox}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="QR code to join the game">'
        f'<rect x="0" y="0" width="{size}" height="{size}" fill="#ffffff"/>'
        f"{path}"
        f"</svg>"
    )


def shade_hex(hex_color, amount):
    """Lighten (positive amount) or darken (negative amount) a hex color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r = max(0, min(255, r + amount))
    g = max(0, min(255, g + amount))
    b = max(0, min(255, b + amount))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------- Database ----------

def get_db():
    if USE_POSTGRES:
        return _PostgresConn(DATABASE_URL)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    if USE_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                player1 TEXT NOT NULL,
                player2 TEXT NOT NULL,
                winner TEXT,
                is_draw INTEGER NOT NULL DEFAULT 0,
                played_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hidden_players (
                name TEXT PRIMARY KEY,
                hidden_at TEXT NOT NULL
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player1 TEXT NOT NULL,
                player2 TEXT NOT NULL,
                winner TEXT,
                is_draw INTEGER NOT NULL DEFAULT 0,
                played_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hidden_players (
                name TEXT PRIMARY KEY,
                hidden_at TEXT NOT NULL
            )
        """)
    conn.commit()
    conn.close()


init_db()


def log_match(player1, player2, winner, is_draw):
    conn = get_db()
    conn.execute(
        "INSERT INTO matches (player1, player2, winner, is_draw, played_at) VALUES (?, ?, ?, ?, ?)",
        (player1, player2, winner, 1 if is_draw else 0, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def hide_player(name):
    conn = get_db()
    if USE_POSTGRES:
        conn.execute(
            "INSERT INTO hidden_players (name, hidden_at) VALUES (?, ?) "
            "ON CONFLICT (name) DO UPDATE SET hidden_at = EXCLUDED.hidden_at",
            (name, datetime.now().isoformat()),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO hidden_players (name, hidden_at) VALUES (?, ?)",
            (name, datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()


def unhide_player(name):
    conn = get_db()
    conn.execute("DELETE FROM hidden_players WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def get_hidden_players():
    conn = get_db()
    rows = conn.execute("SELECT name FROM hidden_players").fetchall()
    conn.close()
    return {row["name"] for row in rows}


def get_hidden_players_list():
    """Full details of removed players, most recently removed first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT name, hidden_at FROM hidden_players ORDER BY hidden_at DESC"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        hidden_dt = datetime.fromisoformat(row["hidden_at"])
        result.append(
            {
                "name": row["name"],
                "hidden_at": hidden_dt.strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
            }
        )
    return result


def get_records(period):
    now = datetime.now()
    if period == "daily":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        since = now - timedelta(days=now.weekday())
        since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        since = datetime.min

    hidden = get_hidden_players()

    conn = get_db()
    rows = conn.execute(
        "SELECT player1, player2, winner, is_draw FROM matches WHERE played_at >= ?",
        (since.isoformat(),),
    ).fetchall()
    conn.close()

    stats = {}
    for row in rows:
        for name in (row["player1"], row["player2"]):
            if name in hidden:
                continue
            if name not in stats:
                stats[name] = {"name": name, "games": 0, "wins": 0, "draws": 0}

        if row["player1"] not in hidden:
            stats[row["player1"]]["games"] += 1
        if row["player2"] not in hidden:
            stats[row["player2"]]["games"] += 1

        if row["is_draw"]:
            if row["player1"] not in hidden:
                stats[row["player1"]]["draws"] += 1
            if row["player2"] not in hidden:
                stats[row["player2"]]["draws"] += 1
        elif row["winner"] and row["winner"] not in hidden:
            stats[row["winner"]]["wins"] += 1

    leaderboard = sorted(
        stats.values(), key=lambda s: (-s["wins"], -s["games"], s["name"])
    )
    return leaderboard


def get_weekly_breakdown():
    """Returns one entry per day, Monday through Sunday, for the current week."""
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    hidden = get_hidden_players()

    conn = get_db()
    days = []
    for i in range(7):
        day_start = monday + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        rows = conn.execute(
            "SELECT player1, player2, winner, is_draw FROM matches "
            "WHERE played_at >= ? AND played_at < ?",
            (day_start.isoformat(), day_end.isoformat()),
        ).fetchall()

        wins = {}
        for row in rows:
            if row["is_draw"]:
                continue
            winner_name = row["winner"]
            if winner_name and winner_name not in hidden:
                wins[winner_name] = wins.get(winner_name, 0) + 1

        top_player = None
        top_wins = 0
        for name, count in wins.items():
            if count > top_wins:
                top_player = name
                top_wins = count

        days.append(
            {
                "label": day_start.strftime("%a"),
                "date": day_start.strftime("%b %d"),
                "date_iso": day_start.strftime("%Y-%m-%d"),
                "games": len(rows),
                "top_player": top_player,
                "top_wins": top_wins,
                "is_today": day_start.date() == now.date(),
            }
        )
    conn.close()
    return days


def get_monthly_breakdown(year=None, month=None):
    """Returns a calendar-style breakdown (weeks of days) for the given month,
    defaulting to the current month, along with prev/next navigation info."""
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    first_weekday, days_in_month = calendar.monthrange(year, month)  # 0 = Monday

    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    hidden = get_hidden_players()

    conn = get_db()
    rows = conn.execute(
        "SELECT player1, player2, winner, is_draw, played_at FROM matches "
        "WHERE played_at >= ? AND played_at < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    conn.close()

    # Tally games played and wins for each day number in the month
    per_day = {d: {"games": 0, "wins": {}} for d in range(1, days_in_month + 1)}
    for row in rows:
        played_dt = datetime.fromisoformat(row["played_at"])
        d = played_dt.day
        per_day[d]["games"] += 1
        if not row["is_draw"] and row["winner"] and row["winner"] not in hidden:
            per_day[d]["wins"][row["winner"]] = per_day[d]["wins"].get(row["winner"], 0) + 1

    day_cells = {}
    for d in range(1, days_in_month + 1):
        wins = per_day[d]["wins"]
        top_player = None
        top_wins = 0
        for name, count in wins.items():
            if count > top_wins:
                top_player = name
                top_wins = count

        weekday = (first_weekday + d - 1) % 7  # 0 = Monday ... 6 = Sunday

        day_cells[d] = {
            "day": d,
            "date_iso": f"{year:04d}-{month:02d}-{d:02d}",
            "games": per_day[d]["games"],
            "top_player": top_player,
            "top_wins": top_wins,
            "is_today": (year == now.year and month == now.month and d == now.day),
            "is_weekend": weekday in (5, 6),
        }

    # Lay the days out into calendar weeks, Monday first, padding with None
    weeks = []
    week = [None] * first_weekday
    for d in range(1, days_in_month + 1):
        week.append(day_cells[d])
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append(None)
        weeks.append(week)

    # Previous/next month for navigation
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    total_games = sum(cell["games"] for cell in day_cells.values())

    return {
        "month_name": month_start.strftime("%B %Y"),
        "weeks": weeks,
        "year": year,
        "month": month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "is_current_month": (year == now.year and month == now.month),
        "total_games": total_games,
    }


def get_matches_for_date(date_str):
    """Returns the raw list of matches played on a given YYYY-MM-DD date."""
    day_start = datetime.strptime(date_str, "%Y-%m-%d")
    day_end = day_start + timedelta(days=1)

    conn = get_db()
    rows = conn.execute(
        "SELECT player1, player2, winner, is_draw, played_at FROM matches "
        "WHERE played_at >= ? AND played_at < ? ORDER BY played_at",
        (day_start.isoformat(), day_end.isoformat()),
    ).fetchall()
    conn.close()

    matches = []
    for row in rows:
        played_dt = datetime.fromisoformat(row["played_at"])
        matches.append(
            {
                "player1": row["player1"],
                "player2": row["player2"],
                "winner": row["winner"],
                "is_draw": bool(row["is_draw"]),
                "time": played_dt.strftime("%I:%M %p").lstrip("0"),
            }
        )
    return matches


def get_alltime_summary():
    """Total games ever played and the date of the very first game."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as cnt, MIN(played_at) as first_date FROM matches"
    ).fetchone()
    conn.close()

    total_games = row["cnt"] or 0
    first_date = None
    if row["first_date"]:
        first_date = datetime.fromisoformat(row["first_date"]).strftime("%b %d, %Y")

    return {"total_games": total_games, "first_date": first_date}


# ---------- Game state helpers ----------

def new_board():
    return [[0 for _ in range(COLS)] for _ in range(ROWS)]


def drop_piece(board, col, player):
    for row in range(ROWS - 1, -1, -1):
        if board[row][col] == 0:
            board[row][col] = player
            return row
    return None


def check_winner(board, row, col, player):
    """Returns the list of (row, col) cells forming the winning line of 4+
    (so the frontend can highlight exactly those pieces), or None if the
    move just placed doesn't win."""
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        cells = [(row, col)]
        r, c = row + dr, col + dc
        while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == player:
            cells.append((r, c))
            r += dr
            c += dc
        r, c = row - dr, col - dc
        while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == player:
            cells.append((r, c))
            r -= dr
            c -= dc
        if len(cells) >= 4:
            return cells
    return None


def is_draw(board):
    return all(board[0][c] != 0 for c in range(COLS))


# ---------- PWA: manifest + service worker ----------

@app.route("/manifest.json")
def web_manifest():
    manifest = {
        "name": "Connect Four",
        "short_name": "Connect4",
        "description": "Play Connect Four with a friend on another device, anywhere.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#bae6fd",
        "theme_color": "#3b82f6",
        "orientation": "portrait",
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-192-maskable.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-512-maskable.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(manifest), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    # Served from the root (not /static/sw.js) so its scope covers the whole
    # site instead of just the static folder.
    resp = app.send_static_file("sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ---------- Routes ----------

def require_admin(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("records_login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    room_id = session.get("room_id")
    if room_id and get_game(room_id):
        return redirect(url_for("game", room_id=room_id))
    return redirect(url_for("create_game_route"))


@app.route("/create", methods=["GET", "POST"])
def create_game_route():
    if request.method == "GET":
        return render_template(
            "create.html", palette=COLOR_PALETTE, selected_color=COLOR_PALETTE[0]["hex"]
        )

    p1 = request.form.get("player1", "").strip()[:20] or "Red"
    color1 = request.form.get("color1", "").strip()
    if color1.lower() not in PALETTE_HEXES:
        color1 = DEFAULT_COLOR_1

    room_id = create_game(p1, color1)

    session.clear()
    session["room_id"] = room_id
    session["player_num"] = 1
    return redirect(url_for("game", room_id=room_id))


@app.route("/join/<room_id>", methods=["GET", "POST"])
def join_game(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return render_template("join.html", room_id=room_id, error="not_found"), 404

    # If this browser is already player 1 or already-joined player 2 for this
    # exact room, just send it straight to the board instead of re-joining.
    if session.get("room_id") == room_id and session.get("player_num") in (1, 2):
        return redirect(url_for("game", room_id=room_id))

    if game["player2_joined"]:
        return render_template("join.html", room_id=room_id, error="full"), 409

    taken_color = game["player1_color"]
    taken_name = game["player1_name"]
    default_color = next(
        (c["hex"] for c in COLOR_PALETTE if c["hex"].lower() != taken_color.lower()),
        COLOR_PALETTE[0]["hex"],
    )

    if request.method == "GET":
        return render_template(
            "join.html",
            room_id=room_id,
            host_name=taken_name,
            error=None,
            palette=COLOR_PALETTE,
            taken_color=taken_color,
            taken_name=taken_name,
            selected_color=default_color,
            entered_name="",
        )

    p2 = request.form.get("player2", "").strip()[:20]
    color2 = request.form.get("color2", "").strip()

    field_errors = []
    if not p2:
        field_errors.append("Please enter your name.")
    elif p2.lower() == taken_name.strip().lower():
        field_errors.append(
            f'The name "{taken_name}" is already taken by the other player — pick a different name.'
        )

    if color2.lower() not in PALETTE_HEXES:
        field_errors.append("Please pick one of the colors below.")
        color2 = default_color
    elif color2.lower() == taken_color.lower():
        field_errors.append(f"{taken_name} already chose that color — pick another one.")

    if field_errors:
        return render_template(
            "join.html",
            room_id=room_id,
            host_name=taken_name,
            error=None,
            palette=COLOR_PALETTE,
            taken_color=taken_color,
            taken_name=taken_name,
            selected_color=color2,
            entered_name=p2,
            field_errors=field_errors,
        )

    game["player2_name"] = p2
    game["player2_color"] = color2
    game["player2_joined"] = True

    session.clear()
    session["room_id"] = room_id
    session["player_num"] = 2
    return redirect(url_for("game", room_id=room_id))


@app.route("/game/<room_id>")
def game(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return redirect(url_for("create_game_route"))

    # Whoever's browser this is needs to actually belong to the room (as
    # player 1 or player 2) before we show them the board.
    if session.get("room_id") != room_id or session.get("player_num") not in (1, 2):
        return redirect(url_for("join_game", room_id=room_id))

    player_num = session["player_num"]
    color1 = game["player1_color"]
    color2 = game["player2_color"] or DEFAULT_COLOR_2

    return render_template(
        "index.html",
        room_id=room_id,
        player_num=player_num,
        join_url=url_for("join_game", room_id=room_id, _external=True),
        join_qr_svg=generate_qr_svg(url_for("join_game", room_id=room_id, _external=True)),
        board=game["board"],
        turn=game["turn"],
        winner=game["winner"],
        scores=game["scores"],
        player1_name=game["player1_name"],
        player2_name=game["player2_name"] or "Waiting…",
        player2_joined=game["player2_joined"],
        player1_color=color1,
        player2_color=color2,
        player1_color_light=shade_hex(color1, 60),
        player1_color_dark=shade_hex(color1, -50),
        player2_color_light=shade_hex(color2, 60),
        player2_color_dark=shade_hex(color2, -50),
    )


@app.route("/state/<room_id>")
def state(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return jsonify(error="Game not found"), 404
    return jsonify(game_state_json(game))


@app.route("/move/<room_id>", methods=["POST"])
def move(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return jsonify(error="Game not found"), 404
    if session.get("room_id") != room_id or session.get("player_num") not in (1, 2):
        return jsonify(error="You're not a player in this game"), 403
    if not game["player2_joined"]:
        return jsonify(error="Waiting for the other player to join"), 400

    player_num = session["player_num"]
    board, turn, winner = game["board"], game["turn"], game["winner"]

    if winner != 0:
        return jsonify(game_state_json(game))

    if player_num != turn:
        return jsonify(error="It's not your turn"), 409

    col = int((request.json or {}).get("col", -1))
    if col < 0 or col >= COLS:
        return jsonify(error="Invalid column"), 400

    row = drop_piece(board, col, turn)
    if row is None:
        return jsonify(game_state_json(game))

    p1 = game["player1_name"]
    p2 = game["player2_name"]

    winning_cells = check_winner(board, row, col, turn)
    if winning_cells:
        winner = turn
        game["scores"][str(turn)] += 1
        game["winning_cells"] = winning_cells
        winner_name = p1 if turn == 1 else p2
        log_match(p1, p2, winner_name, is_draw=False)
    elif is_draw(board):
        winner = 3
        log_match(p1, p2, winner=None, is_draw=True)
    else:
        turn = 2 if turn == 1 else 1

    game["turn"] = turn
    game["winner"] = winner
    prev_move_id = (game.get("last_move") or {}).get("move_id", 0)
    game["last_move"] = {"row": row, "col": col, "move_id": prev_move_id + 1}

    return jsonify(game_state_json(game))


@app.route("/reset/<room_id>", methods=["POST"])
def reset(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return jsonify(error="Game not found"), 404
    if session.get("room_id") != room_id or session.get("player_num") not in (1, 2):
        return jsonify(error="You're not a player in this game"), 403

    game["board"] = new_board()
    game["turn"] = 1
    game["winner"] = 0
    game["last_move"] = None
    game["winning_cells"] = None
    return jsonify(game_state_json(game))


@app.route("/reset_scores/<room_id>", methods=["POST"])
def reset_scores(room_id):
    room_id = room_id.upper()
    game = get_game(room_id)
    if not game:
        return jsonify(error="Game not found"), 404
    if session.get("room_id") != room_id or session.get("player_num") not in (1, 2):
        return jsonify(error="You're not a player in this game"), 403

    game["scores"] = {"1": 0, "2": 0}
    return jsonify(game_state_json(game))


@app.route("/new_players")
def new_players():
    session.clear()
    return redirect(url_for("create_game_route"))


@app.route("/records")
@require_admin
def records():
    period = request.args.get("period", "daily")
    if period not in ("daily", "weekly", "monthly", "alltime"):
        period = "daily"
    leaderboard = get_records(period)
    weekly_breakdown = get_weekly_breakdown() if period == "weekly" else None

    monthly_breakdown = None
    if period == "monthly":
        year = request.args.get("year", type=int)
        month = request.args.get("month", type=int)
        monthly_breakdown = get_monthly_breakdown(year, month)

    alltime_summary = get_alltime_summary() if period == "alltime" else None
    hidden_list = get_hidden_players_list()

    return render_template(
        "records.html",
        leaderboard=leaderboard,
        period=period,
        weekly_breakdown=weekly_breakdown,
        monthly_breakdown=monthly_breakdown,
        alltime_summary=alltime_summary,
        hidden_list=hidden_list,
    )


@app.route("/records/day")
@require_admin
def records_day():
    date_str = request.args.get("date", "")
    try:
        matches = get_matches_for_date(date_str)
    except ValueError:
        return jsonify(error="Invalid date"), 400
    return jsonify(date=date_str, matches=matches)


@app.route("/records/hide", methods=["POST"])
@require_admin
def records_hide():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify(error="Missing name"), 400
    hide_player(name)
    return jsonify(ok=True, name=name)


@app.route("/records/unhide", methods=["POST"])
@require_admin
def records_unhide():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify(error="Missing name"), 400
    unhide_player(name)
    return jsonify(ok=True, name=name)


@app.route("/records/login", methods=["GET", "POST"])
def records_login():
    error = None
    if request.method == "POST":
        entered = request.form.get("password", "")
        if entered == ADMIN_PASSWORD:
            session["is_admin"] = True
            next_url = request.args.get("next") or url_for("records")
            return redirect(next_url)
        error = "Wrong password."
    return render_template("records_login.html", error=error)


@app.route("/records/logout")
def records_logout():
    session.pop("is_admin", None)
    return redirect(url_for("records_login"))


if __name__ == "__main__":
    # host="0.0.0.0" makes the server reachable from other devices on the
    # same network (not just this computer). threaded=True lets it handle
    # several PCs/players hitting it at the same time without one request
    # blocking another.
    #
    # PORT is read from the environment so this also works unchanged on
    # hosting platforms (Render, Railway, etc.) that assign their own port.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)