const drawButton = document.querySelector("#draw-button");
const resetButton = document.querySelector("#reset-button");
const drawCountInput = document.querySelector("#draw-count");
const result = document.querySelector("#result");
const drawDisplay = document.querySelector(".draw-display");

const fallbackParticipants = [
  {name: "抽奖中", number: 1},
  {name: "抽奖中", number: 8},
  {name: "抽奖中", number: 18},
  {name: "抽奖中", number: 28},
  {name: "抽奖中", number: 58},
];

const sleep = (delay) => new Promise((resolve) => window.setTimeout(resolve, delay));

function normalizeDrawCount() {
  const count = Number.parseInt(drawCountInput.value, 10);
  if (!Number.isInteger(count) || count < 1) {
    return 1;
  }
  return count;
}

async function fetchAvailableParticipants() {
  const response = await fetch("/api/participants");
  const body = await response.json();
  if (!body.success || body.data.participants.length === 0) {
    return fallbackParticipants;
  }
  return body.data.participants;
}

function buildDisplaySlots(count) {
  drawDisplay.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const slot = document.createElement("div");
    slot.className = "winner-slot";

    const number = document.createElement("div");
    number.className = "rolling-number";
    number.textContent = "--";

    const name = document.createElement("div");
    name.className = "rolling-name";
    name.textContent = "等待抽奖";

    slot.append(number, name);
    drawDisplay.append(slot);
  }
}

function showParticipant(slot, participant) {
  slot.querySelector(".rolling-number").textContent = String(participant.number).padStart(2, "0");
  slot.querySelector(".rolling-name").textContent = participant.name;
}

function startRolling(participants, count) {
  buildDisplaySlots(count);
  const slots = Array.from(drawDisplay.querySelectorAll(".winner-slot"));
  drawDisplay.classList.remove("winner");
  drawDisplay.classList.add("rolling");
  slots.forEach((slot, slotIndex) => showParticipant(slot, participants[slotIndex % participants.length]));
  return window.setInterval(() => {
    slots.forEach((slot, slotIndex) => {
      const offset = Math.floor(Date.now() / 80) + slotIndex;
      showParticipant(slot, participants[offset % participants.length]);
    });
  }, 80);
}

async function stopRolling(intervalId, participants, winners) {
  window.clearInterval(intervalId);
  const slots = Array.from(drawDisplay.querySelectorAll(".winner-slot"));
  const delays = [70, 80, 90, 110, 130, 160, 190, 230, 280, 340, 420, 520];

  for (let index = 0; index < delays.length; index += 1) {
    slots.forEach((slot, slotIndex) => {
      const winner = winners[slotIndex];
      const reel = [...participants, ...participants, ...participants, winner];
      showParticipant(slot, reel[(index + slotIndex) % reel.length]);
    });
    await sleep(delays[index]);
  }

  slots.forEach((slot, index) => showParticipant(slot, winners[index]));
  drawDisplay.classList.remove("rolling");
  drawDisplay.classList.add("winner");
}

drawButton.addEventListener("click", async () => {
  drawButton.disabled = true;
  resetButton.disabled = true;
  drawCountInput.disabled = true;
  result.textContent = "抽奖中...";
  let rollingTimer = null;

  try {
    const count = normalizeDrawCount();
    drawCountInput.value = count;
    const participants = await fetchAvailableParticipants();
    rollingTimer = startRolling(participants, count);
    const response = await fetch("/api/draw", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({count, exclude_winners: true}),
    });
    const body = await response.json();
    if (body.success) {
      const winners = body.data.winners || [body.data.winner];
      await stopRolling(rollingTimer, participants, winners);
      rollingTimer = null;
      const winnerText = winners.map((winner) => `${winner.name}，号码 ${winner.number}`).join("；");
      result.textContent = `第 ${body.data.draw.round} 轮：${winnerText}`;
    } else {
      if (rollingTimer) {
        window.clearInterval(rollingTimer);
      }
      drawDisplay.classList.remove("rolling", "winner");
      result.textContent = body.error.message;
    }
  } catch (_) {
    if (rollingTimer) {
      window.clearInterval(rollingTimer);
    }
    drawDisplay.classList.remove("rolling", "winner");
    result.textContent = "系统繁忙，请稍后重试";
  } finally {
    drawButton.disabled = false;
    resetButton.disabled = false;
    drawCountInput.disabled = false;
  }
});

resetButton.addEventListener("click", async () => {
  if (!window.confirm("确认重置活动数据？")) {
    return;
  }

  resetButton.disabled = true;
  drawButton.disabled = true;
  drawCountInput.disabled = true;
  drawDisplay.classList.remove("rolling", "winner");
  buildDisplaySlots(normalizeDrawCount());
  result.textContent = "重置中...";

  try {
    const response = await fetch("/api/reset", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: true, reset_scope: "all"}),
    });
    const body = await response.json();
    result.textContent = body.success ? "活动已重置" : body.error.message;
  } catch (_) {
    result.textContent = "系统繁忙，请稍后重试";
  } finally {
    resetButton.disabled = false;
    drawButton.disabled = false;
    drawCountInput.disabled = false;
  }
});

drawCountInput.addEventListener("change", () => {
  const count = normalizeDrawCount();
  drawCountInput.value = count;
  drawDisplay.classList.remove("rolling", "winner");
  buildDisplaySlots(count);
});
