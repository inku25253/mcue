import { test } from "node:test";
import assert from "node:assert/strict";
import { W, H, newGame, move, rotate, step, hardDrop } from "./scripts/tetris.js";

// rand() = 0 makes the first piece of the bag an I.
const game = () => {
  const g = newGame(() => 0);
  assert.equal(g.piece.type, "I");
  return g;
};

test("a vertical I fills the gap and clears the bottom row", () => {
  const g = game();
  g.board.fill(1, (H - 1) * W, H * W - 1); // bottom row except the last column
  rotate(g); // vertical in column 5
  for (let i = 0; i < 4; i++) assert.ok(move(g, 1, 0));
  assert.equal(move(g, 1, 0), false); // wall
  hardDrop(g);
  assert.equal(g.lines, 1);
  assert.equal(g.score, 16 * 2 + 100);
  assert.deepEqual(g.board.slice((H - 1) * W), [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]);
});

test("rotating against the wall kicks the piece back inside", () => {
  const g = game();
  rotate(g);
  while (move(g, -1, 0));
  assert.ok(rotate(g));
  assert.ok(g.piece.cells.every(([x]) => g.piece.x + x >= 0 && g.piece.x + x < W));
});

test("a blocked spawn ends the game", () => {
  const g = game();
  for (let y = 0; y < H; y++) g.board[y * W + 4] = 1;
  step(g);
  assert.ok(g.over);
});
