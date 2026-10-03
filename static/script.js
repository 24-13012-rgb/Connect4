const board = document.getElementById("board");
const statusEl = document.getElementById("status");
const score1El = document.getElementById("score1");
const score2El = document.getElementById("score2");
const winModal = document.getElementById("winModal");
const winText = document.getElementById("winText");

let currentTurn = typeof INITIAL_TURN !== "undefined" ? INITIAL_TURN : 1;
let currentWinner = typeof INITIAL_WINNER !== "undefined" ? INITIAL_WINNER : 0;
let opponentJoined = typeof PLAYER2_JOINED !== "undefined" ? PLAYER2_JOINED : true;
let lastSeenMoveId = 0;
let winModalShownForWinner = 0;

// ---------- Waiting-for-opponent screen ----------
// If the second player hasn't joined this room yet, poll the server and
// reload the page the moment they do, so the board appears automatically
// without the first player having to do anything.
const copyLinkBtn = document.getElementById("copyLinkBtn");
if (copyLinkBtn) {
  copyLinkBtn.addEventListener("click", async () => {
    const input = document.getElementById("joinLinkInput");
    input.select();
    input.setSelectionRange(0, 99999);
    try {
      await navigator.clipboard.writeText(input.value);
      copyLinkBtn.textContent = "✅ Copied!";
    } catch (e) {
      document.execCommand("copy");
      copyLinkBtn.textContent = "✅ Copied!";
    }
    setTimeout(() => (copyLinkBtn.textContent = "📋 Copy"), 1500);
  });
}

if (!opponentJoined && typeof ROOM_ID !== "undefined") {
  const waitForOpponent = setInterval(async () => {
    try {
      const res = await fetch(`/state/${ROOM_ID}`);
      if (!res.ok) return;
      const state = await res.json();
      if (state.player2_joined) {
        clearInterval(waitForOpponent);
        location.reload();
      }
    } catch (e) {
      // network hiccup, just try again next tick
    }
  }, 1500);
}

// The join link lives in a modal instead of an always-visible panel, so it
// doesn't push the board down. It opens automatically once so the host
// doesn't miss it, and can be reopened any time via the "Share Join Link"
// button.
const joinLinkModal = document.getElementById("joinLinkModal");
const openJoinLinkBtn = document.getElementById("openJoinLinkBtn");
const closeJoinLinkBtn = document.getElementById("closeJoinLinkBtn");

if (joinLinkModal) {
  joinLinkModal.classList.remove("hidden");

  if (openJoinLinkBtn) {
    openJoinLinkBtn.addEventListener("click", () => {
      joinLinkModal.classList.remove("hidden");
    });
  }
  if (closeJoinLinkBtn) {
    closeJoinLinkBtn.addEventListener("click", () => {
      joinLinkModal.classList.add("hidden");
    });
  }
  // Clicking the dimmed backdrop (not the card itself) also closes it.
  joinLinkModal.addEventListener("click", (e) => {
    if (e.target === joinLinkModal) joinLinkModal.classList.add("hidden");
  });
}

// Keep track of the last board we painted so we only touch cells that
// actually changed value — this is what stops every piece on the board
// from re-triggering the drop/bounce animation on every single move.
//
// This always starts as all-empty, even when the game already has pieces
// on it (e.g. opening/refreshing a page mid-game) — the page itself always
// renders every cell visually empty at first (the real board only gets
// painted in by the first poll below), so this must start empty too, or
// the diff against the real board would see no change and leave those
// pieces permanently invisible until the next move.
let previousBoard = Array.from({ length: 6 }, () => Array(7).fill(0));

function colorInfoFor(player) {
  if (player === 1) return { base: PLAYER1_COLOR, light: PLAYER1_COLOR_LIGHT };
  if (player === 2) return { base: PLAYER2_COLOR, light: PLAYER2_COLOR_LIGHT };
  return null;
}

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function updateStatus(turn, winner) {
  if (winner === 1) {
    statusEl.textContent = PLAYER1_NAME + " Wins!";
  } else if (winner === 2) {
    statusEl.textContent = PLAYER2_NAME + " Wins!";
  } else if (winner === 3) {
    statusEl.textContent = "Draw!";
  } else if (typeof opponentJoined !== "undefined" && !opponentJoined) {
    statusEl.textContent = "Waiting for opponent…";
  } else {
    const name = turn === 1 ? PLAYER1_NAME : PLAYER2_NAME;
    const isYou = typeof PLAYER_NUM !== "undefined" && turn === PLAYER_NUM;
    statusEl.textContent = isYou ? "Your Turn (" + name + ")" : name + "'s Turn";
  }
}

// paintCell owns the piece's REAL color, exclusively. Nothing else in this
// file is ever allowed to touch cellEl.style.background — that separation
// is what makes the preview/flash tint below impossible to confuse with an
// actual placed piece.
function paintCell(cellEl, value) {
  cellEl.classList.remove("preview-tint");
  cellEl.style.removeProperty("--preview-color");

  if (value === 0) {
    cellEl.className = "piece empty";
    cellEl.style.background = "";
    return;
  }
  const info = colorInfoFor(value);
  cellEl.className = "piece filled";
  cellEl.style.background = `radial-gradient(circle at 35% 30%, ${info.light}, ${info.base})`;
}

function renderBoard(state) {
  // Belt-and-suspenders: wipe any hover/keyboard preview tint the instant
  // fresh server state arrives, so nothing stale can ever survive a render.
  clearColumnPreview();

  for (let r = 0; r < 6; r++) {
    for (let c = 0; c < 7; c++) {
      const newVal = state.board[r][c];
      const oldVal = previousBoard[r][c];

      // Only touch the DOM (and therefore only trigger the CSS animation)
      // for the cell whose value actually changed.
      if (newVal !== oldVal) {
        const cell = document.getElementById(`cell-${r}-${c}`);
        paintCell(cell, newVal);
      }
    }
  }

  previousBoard = state.board.map((row) => row.slice());

  // Highlight the four connected pieces that won the round. This runs
  // separately from the diff loop above because most of those pieces were
  // placed on earlier moves, so they won't show up as "changed" on the
  // winning move's render -- they still need the glow added explicitly.
  // The piece that just completed the win (state.row/state.col) is the
  // exception: it's skipped here and highlighted later, once its drop
  // animation actually finishes (see attachLandingHandler below), so the
  // glow doesn't cut its falling animation short.
  if (state.winner && state.winner !== 0 && Array.isArray(state.winning_cells)) {
    for (const cell of state.winning_cells) {
      const [r, c] = cell;
      if (r === state.row && c === state.col) continue;
      const cellEl = document.getElementById(`cell-${r}-${c}`);
      if (cellEl) cellEl.classList.add("win");
    }
  }

  if (typeof state.player2_joined === "boolean" && state.player2_joined !== opponentJoined) {
    opponentJoined = state.player2_joined;
    board.classList.toggle("board-disabled", !opponentJoined);
    if (opponentJoined) {
      const waitingBar = document.getElementById("waitingBar");
      if (waitingBar) waitingBar.remove();
      const joinLinkModal = document.getElementById("joinLinkModal");
      if (joinLinkModal) joinLinkModal.remove();
    }
  }

  updateStatus(state.turn, state.winner);
  score1El.textContent = state.scores["1"];
  score2El.textContent = state.scores["2"];

  currentTurn = state.turn;
  currentWinner = state.winner;

  // Wire up the landing effects to fire exactly when the animation finishes,
  // only for the single piece that was just dropped -- guarded by move_id so
  // a repeat poll of the same state never re-triggers it.
  const moveId = typeof state.move_id === "number" ? state.move_id : 0;
  if (
    moveId > lastSeenMoveId &&
    typeof state.row === "number" &&
    typeof state.col === "number"
  ) {
    lastSeenMoveId = moveId;
    const landedColor = state.board[state.row][state.col];
    const isWinningDrop =
      state.winner &&
      state.winner !== 0 &&
      Array.isArray(state.winning_cells) &&
      state.winning_cells.some(([r, c]) => r === state.row && c === state.col);
    attachLandingHandler(state.row, state.col, landedColor, isWinningDrop);
  }

  // Only pop the modal once per new win/draw, so repeated polling (or the
  // opponent's device) doesn't keep re-opening it after someone closes it.
  if (state.winner && state.winner !== 0 && state.winner !== winModalShownForWinner) {
    winModalShownForWinner = state.winner;
    setTimeout(() => showWinModal(state.winner), 500);
    if (state.winner !== 3 && Array.isArray(state.winning_cells)) {
      // Same 500ms beat as the modal above, so the connecting line draws in
      // right as the winning piece finishes landing, not mid-fall.
      setTimeout(() => drawWinLine(state.winning_cells), 500);
    }
  }
  if (!state.winner || state.winner === 0) {
    winModalShownForWinner = 0;
    clearWinLine();
  }
}

// Draws a glowing line through the centers of the four (or more) connected
// winning pieces, so it's visually obvious *how* someone won, not just
// that the individual pieces are highlighted.
let winLineCells = null;

function drawWinLine(winningCells) {
  const svg = document.getElementById("winLineSvg");
  const line = document.getElementById("winLine");
  if (!svg || !line || !Array.isArray(winningCells) || winningCells.length < 2) return;

  winLineCells = winningCells;

  const first = winningCells[0];
  const last = winningCells[winningCells.length - 1];
  const firstEl = document.getElementById(`cell-${first[0]}-${first[1]}`);
  const lastEl = document.getElementById(`cell-${last[0]}-${last[1]}`);
  if (!firstEl || !lastEl) return;

  // Must unhide (display:none -> flex/svg) BEFORE measuring anything below:
  // a display:none element's getBoundingClientRect() is always all-zeros,
  // which would silently throw off every coordinate calculated from it.
  svg.classList.remove("hidden");

  const boardRect = svg.getBoundingClientRect();
  const r1 = firstEl.getBoundingClientRect();
  const r2 = lastEl.getBoundingClientRect();
  const x1 = r1.left + r1.width / 2 - boardRect.left;
  const y1 = r1.top + r1.height / 2 - boardRect.top;
  const x2 = r2.left + r2.width / 2 - boardRect.left;
  const y2 = r2.top + r2.height / 2 - boardRect.top;

  // Scale the line's thickness with the actual rendered cell size, so it
  // looks right whether the board is phone-sized or desktop-sized.
  line.setAttribute("stroke-width", Math.max(6, r1.width * 0.16));
  line.setAttribute("x1", x1);
  line.setAttribute("y1", y1);
  line.setAttribute("x2", x2);
  line.setAttribute("y2", y2);

  // "Draw" the line in from one end rather than just popping in.
  const length = Math.hypot(x2 - x1, y2 - y1);
  line.style.transition = "none";
  line.style.strokeDasharray = `${length}`;
  line.style.strokeDashoffset = `${length}`;
  // Force a reflow so the browser registers the starting offset before we
  // transition it to 0 -- otherwise both changes get batched together and
  // no animation plays at all.
  void line.getBoundingClientRect();
  line.style.transition = "stroke-dashoffset 0.5s ease";
  line.style.strokeDashoffset = "0";
}

function clearWinLine() {
  winLineCells = null;
  const svg = document.getElementById("winLineSvg");
  if (svg) svg.classList.add("hidden");
}

// The board resizes with the viewport (see the responsive board CSS), so a
// visible win line needs to be redrawn on resize or it'll point at stale
// coordinates from before the resize.
window.addEventListener("resize", () => {
  if (winLineCells) drawWinLine(winLineCells);
});

function attachLandingHandler(row, col, colorValue, isWinningDrop) {
  const piece = document.getElementById(`cell-${row}-${col}`);
  if (!piece) return;

  const onLand = (e) => {
    // The whole fall + bounce is now one single animation ("dropLand"),
    // so this fires exactly once, exactly when it's truly finished.
    if (e.animationName !== "dropLand") return;
    piece.removeEventListener("animationend", onLand);
    playLandingEffect(piece, colorValue);
    // Add the win glow only after the fall finishes landing -- adding it
    // earlier would switch the piece's animation to the glow's "wiggle"
    // immediately, cutting the drop short.
    if (isWinningDrop) piece.classList.add("win");
  };

  piece.addEventListener("animationend", onLand);
}

function playLandingEffect(piece, colorValue) {
  const info = colorInfoFor(colorValue);
  if (!info) return;

  // Glow ring pulse, colored to match this player's chosen color
  const ring = document.createElement("div");
  ring.className = "landing-ring";
  ring.style.borderColor = info.base;
  ring.style.boxShadow = `0 0 10px 2px ${hexToRgba(info.base, 0.5)}`;
  piece.appendChild(ring);
  setTimeout(() => ring.remove(), 420);

  // A few tiny spark particles flying outward
  const sparkCount = 5;
  for (let i = 0; i < sparkCount; i++) {
    const spark = document.createElement("div");
    spark.className = "landing-spark";
    const angle = (Math.PI * 2 * i) / sparkCount;
    const distance = 18 + Math.random() * 8;
    spark.style.setProperty("--sx", `${Math.cos(angle) * distance}px`);
    spark.style.setProperty("--sy", `${Math.sin(angle) * distance}px`);
    spark.style.background = info.base;
    piece.appendChild(spark);
    setTimeout(() => spark.remove(), 420);
  }
}

function showWinModal(winner) {
  if (winner === 3) {
    winText.textContent = "🤝 It's a Draw!";
  } else {
    winText.textContent = "🎉 " + (winner === 1 ? PLAYER1_NAME : PLAYER2_NAME) + " Wins!";
  }
  winModal.classList.remove("hidden");
}

// Guards against overlapping moves. Without this, rapidly clicking or
// holding down a key (which auto-repeats) could fire several /move
// requests before the first one finished, and their responses could
// arrive out of order — desyncing the board.
let moveInProgress = false;

function isMyTurn() {
  return (
    opponentJoined &&
    (!currentWinner || currentWinner === 0) &&
    (typeof PLAYER_NUM === "undefined" || currentTurn === PLAYER_NUM)
  );
}

async function makeMove(col) {
  if (moveInProgress) return;
  if (!isMyTurn()) return;
  moveInProgress = true;
  clearColumnPreview();
  try {
    const res = await fetch(`/move/${ROOM_ID}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ col }),
    });
    const state = await res.json();
    if (state.error) return;
    renderBoard(state);
  } finally {
    moveInProgress = false;
  }
}

board.addEventListener("click", (e) => {
  const cell = e.target.closest(".cell");
  if (!cell) return;
  const col = parseInt(cell.dataset.col);
  makeMove(col);
});

// ---------- Column preview tint (mouse hover AND keyboard) ----------
// This lives entirely on a ::after overlay driven by a class + CSS variable
// (see style.css, ".piece.preview-tint::after"). It never touches
// cellEl.style.background, which paintCell owns exclusively for the piece's
// real color — so a preview tint and a real piece can never collide or get
// left stuck on top of each other again.
function clearColumnPreview() {
  document.querySelectorAll(".piece.preview-tint").forEach((el) => {
    el.classList.remove("preview-tint");
    el.style.removeProperty("--preview-color");
  });
}

function previewColumn(col) {
  // Never show a preview while a move is actually being processed — this
  // is what closes the timing gap that could leave a stuck tinted "ghost"
  // cell if the mouse happened to be resting over the board mid-drop.
  if (moveInProgress) return;
  if (!isMyTurn()) return;
  clearColumnPreview();

  const info = colorInfoFor(currentTurn);
  if (!info) return;

  document.querySelectorAll(`.cell[data-col="${col}"] .piece.empty`).forEach((el) => {
    el.style.setProperty("--preview-color", info.base);
    el.classList.add("preview-tint");
  });
}

board.addEventListener("mouseover", (e) => {
  const cell = e.target.closest(".cell");
  if (!cell) return;
  previewColumn(cell.dataset.col);
});

board.addEventListener("mouseleave", () => {
  clearColumnPreview();
});

// ---------- Keyboard controls ----------
// Q W E R T Y U map to columns 1-7 (they sit in a row, matching the board).
// Number keys 1-7 work too, for people who reach for those instead.
const KEY_TO_COLUMN = {
  q: 0, w: 1, e: 2, r: 3, t: 4, y: 5, u: 6,
  1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6,
};

document.addEventListener("keydown", (e) => {
  // Don't hijack typing in a text box, or shortcuts like Ctrl+R
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea") return;

  // Ignore the browser's auto-repeat when a key is held down.
  if (e.repeat) return;

  const key = e.key.toLowerCase();
  const col = KEY_TO_COLUMN[key];
  if (col === undefined) return;

  e.preventDefault();
  if (!isMyTurn()) return;
  if (moveInProgress) return;

  previewColumn(col);
  makeMove(col);
});

async function newRound() {
  const res = await fetch(`/reset/${ROOM_ID}`, { method: "POST" });
  const state = await res.json();
  if (state.error) return;
  renderBoard(state);
  winModal.classList.add("hidden");
}

async function resetScores() {
  const res = await fetch(`/reset_scores/${ROOM_ID}`, { method: "POST" });
  const data = await res.json();
  if (data.error) return;
  score1El.textContent = data.scores["1"];
  score2El.textContent = data.scores["2"];
}

document.getElementById("newRoundBtn").addEventListener("click", newRound);
document.getElementById("resetScoresBtn").addEventListener("click", resetScores);
document.getElementById("playAgainBtn").addEventListener("click", newRound);

// ---------- Live sync with the other device ----------
// Polls the shared game room so a move made on one device shows up on the
// other's screen (and vice versa) without anyone refreshing the page.
if (typeof ROOM_ID !== "undefined") {
  setInterval(async () => {
    if (moveInProgress) return;
    try {
      const res = await fetch(`/state/${ROOM_ID}`);
      if (!res.ok) return;
      const state = await res.json();
      if (state.error) return;
      renderBoard(state);
    } catch (e) {
      // network hiccup, just try again next tick
    }
  }, 1000);
}