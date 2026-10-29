import { world, system, InputButton, ButtonState, InputPermissionCategory } from "@minecraft/server";
import { W, H, PIECES, newGame, move, rotate, step, hardDrop, cellsOf, dropInterval } from "./tetris.js";

// Formatting codes matching the concrete colors in resource.cue (index = tetris:color).
const COLOR_CODE = ["§f", "§b", "§e", "§5", "§a", "§c", "§9", "§6"];
const games = new Map();

system.afterEvents.scriptEventReceive.subscribe(({ id, sourceEntity }) => {
  if (id === "tetris:start" && sourceEntity?.typeId === "minecraft:player") start(sourceEntity);
});

// The world may have been closed mid-game: unfreeze the player and drop leftover cells.
world.afterEvents.playerSpawn.subscribe(({ player, initialSpawn }) => {
  if (!initialSpawn) return;
  player.inputPermissions.setPermissionCategory(InputPermissionCategory.Movement, true);
  if (games.size === 0) for (const e of player.dimension.getEntities({ type: "tetris:cell" })) e.remove();
});

system.runInterval(() => {
  for (const s of games.values()) tick(s);
});

function start(player) {
  if (games.has(player.id)) stop(games.get(player.id));
  const l = player.location;
  const o = { x: Math.floor(l.x) - 5, y: Math.min(Math.floor(l.y) + 10, 290), z: Math.floor(l.z) - 25 };
  const cells = [];
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      cells.push(player.dimension.spawnEntity("tetris:cell", { x: o.x + x + 0.5, y: o.y + H - 1 - y, z: o.z + 0.5 }));
    }
  }
  // The camera faces north, so screen right is +x.
  player.camera.setCamera("minecraft:free", {
    location: { x: o.x + 5, y: o.y + 10, z: o.z + 27 },
    facingLocation: { x: o.x + 5, y: o.y + 10, z: o.z + 0.5 },
  });
  player.inputPermissions.setPermissionCategory(InputPermissionCategory.Movement, false);
  games.set(player.id, {
    id: player.id,
    player,
    cells,
    game: newGame(),
    shown: new Array(W * H).fill(0),
    hud: "",
    dx: 0,
    das: 0,
    fall: 0,
    up: false,
    jump: false,
    overTicks: 0,
  });
}

function stop(s) {
  games.delete(s.id);
  for (const c of s.cells) if (c.isValid) c.remove();
  const p = s.player;
  if (!p.isValid) return;
  p.camera.clear();
  p.inputPermissions.setPermissionCategory(InputPermissionCategory.Movement, true);
  p.onScreenDisplay.setTitle(""); // an empty title clears it and hides the HUD panel
}

function tick(s) {
  const { player: p, game: g } = s;
  if (!p.isValid) return stop(s);
  if (g.over) {
    if (++s.overTicks > 60) return stop(s);
  } else {
    // Input still arrives while movement is disabled.
    const input = p.inputInfo;
    const v = input.getMovementVector();
    if (input.getButtonState(InputButton.Sneak) === ButtonState.Pressed) return stop(s);
    const dx = v.x > 0.5 ? -1 : v.x < -0.5 ? 1 : 0; // strafing left is +x
    if (dx !== s.dx) {
      s.dx = dx;
      s.das = 0;
      if (dx) move(g, dx, 0);
    } else if (dx && ++s.das >= 6 && s.das % 2 === 0) {
      move(g, dx, 0);
    }
    const up = v.y > 0.5;
    if (up && !s.up) rotate(g);
    s.up = up;
    const jump = input.getButtonState(InputButton.Jump) === ButtonState.Pressed;
    if (jump && !s.jump) {
      hardDrop(g);
      s.fall = 0;
    }
    s.jump = jump;
    const soft = v.y < -0.5;
    if (soft || ++s.fall >= dropInterval(g)) {
      s.fall = 0;
      step(g, soft);
    }
  }
  render(s);
}

function render(s) {
  const g = s.game;
  const now = cellsOf(g);
  now.forEach((c, i) => {
    if (c !== s.shown[i] && s.cells[i].isValid) s.cells[i].setProperty("tetris:color", c);
  });
  s.shown = now;

  // hud_screen.json shows titles starting with "tetris:" in its own panel.
  const hud = [
    "tetris:§l§6TETRIS",
    "",
    `§rScore  §e${g.score}`,
    `§rLines  §e${g.lines}`,
    `§rLevel  §e${g.level}`,
    `§rNext   ${COLOR_CODE[PIECES[g.next][0]]}§l${g.next}`,
    "",
    g.over ? "§c§lGAME OVER" : "§7A/D: move  W: rotate\n§7S: soft drop  Space: drop\n§7Shift: quit",
  ].join("\n");
  if (hud !== s.hud) {
    s.hud = hud;
    s.player.onScreenDisplay.setTitle(hud, { fadeInDuration: 0, stayDuration: 72000, fadeOutDuration: 0 });
  }
}
