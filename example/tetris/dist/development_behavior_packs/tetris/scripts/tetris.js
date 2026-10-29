// Pure Tetris rules. No Minecraft imports, so node can test this file.
export const W = 10;
export const H = 20;

// type: [tetris:color, rotation box size, cells as [x, y] with y pointing down]
export const PIECES = {
  I: [1, 4, [[0, 1], [1, 1], [2, 1], [3, 1]]],
  O: [2, 2, [[0, 0], [1, 0], [0, 1], [1, 1]]],
  T: [3, 3, [[1, 0], [0, 1], [1, 1], [2, 1]]],
  S: [4, 3, [[1, 0], [2, 0], [0, 1], [1, 1]]],
  Z: [5, 3, [[0, 0], [1, 0], [1, 1], [2, 1]]],
  J: [6, 3, [[0, 0], [0, 1], [1, 1], [2, 1]]],
  L: [7, 3, [[2, 0], [0, 1], [1, 1], [2, 1]]],
};
// ponytail: simple kick list instead of full SRS tables; swap in SRS if wall kicks feel off.
const KICKS = [[0, 0], [-1, 0], [1, 0], [0, -1], [-2, 0], [2, 0]];
const CLEAR_SCORE = [0, 100, 300, 500, 800];

export function newGame(rand = Math.random) {
  const g = { board: new Array(W * H).fill(0), bag: [], rand, score: 0, lines: 0, level: 1, over: false };
  g.next = draw(g);
  spawn(g);
  return g;
}

export const dropInterval = (g) => Math.max(2, 20 - 2 * g.level);

export function move(g, dx, dy) {
  const p = g.piece;
  if (g.over || !fits(g, p.cells, p.x + dx, p.y + dy)) return false;
  p.x += dx;
  p.y += dy;
  return true;
}

export function rotate(g) {
  const p = g.piece;
  const cells = p.cells.map(([x, y]) => [p.n - 1 - y, x]);
  for (const [kx, ky] of KICKS) {
    if (!g.over && fits(g, cells, p.x + kx, p.y + ky)) {
      Object.assign(p, { cells, x: p.x + kx, y: p.y + ky });
      return true;
    }
  }
  return false;
}

// One gravity (or soft drop) step: fall a row, or lock and spawn the next piece.
export function step(g, soft = false) {
  if (move(g, 0, 1)) {
    if (soft) g.score += 1;
    return;
  }
  if (!g.over) lock(g);
}

export function hardDrop(g) {
  while (move(g, 0, 1)) g.score += 2;
  if (!g.over) lock(g);
}

// Board plus the falling piece, row-major from the top-left.
export function cellsOf(g) {
  const out = g.board.slice();
  const p = g.piece;
  if (!g.over) {
    for (const [cx, cy] of p.cells) if (p.y + cy >= 0) out[(p.y + cy) * W + p.x + cx] = p.color;
  }
  return out;
}

function draw(g) {
  if (!g.bag.length) {
    g.bag = Object.keys(PIECES);
    for (let i = g.bag.length - 1; i > 0; i--) {
      const j = Math.floor(g.rand() * (i + 1));
      [g.bag[i], g.bag[j]] = [g.bag[j], g.bag[i]];
    }
  }
  return g.bag.pop();
}

function spawn(g) {
  const [color, n, cells] = PIECES[g.next];
  g.piece = { type: g.next, color, n, cells, x: Math.floor((W - n) / 2), y: 0 };
  g.next = draw(g);
  if (!fits(g, g.piece.cells, g.piece.x, g.piece.y)) g.over = true;
}

function fits(g, cells, x, y) {
  return cells.every(([cx, cy]) => {
    const px = x + cx;
    const py = y + cy;
    return px >= 0 && px < W && py < H && (py < 0 || !g.board[py * W + px]);
  });
}

function lock(g) {
  const p = g.piece;
  if (p.cells.some(([, cy]) => p.y + cy < 0)) {
    g.over = true;
    return;
  }
  for (const [cx, cy] of p.cells) g.board[(p.y + cy) * W + p.x + cx] = p.color;
  const kept = [];
  for (let y = 0; y < H; y++) {
    const row = g.board.slice(y * W, (y + 1) * W);
    if (!row.every(Boolean)) kept.push(...row);
  }
  const cleared = H - kept.length / W;
  g.board = new Array(cleared * W).fill(0).concat(kept);
  g.score += CLEAR_SCORE[cleared] * g.level;
  g.lines += cleared;
  g.level = Math.floor(g.lines / 10) + 1;
  spawn(g);
}
