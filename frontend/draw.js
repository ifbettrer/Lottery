const drawButton = document.querySelector("#draw-button");
const resetButton = document.querySelector("#reset-button");
const result = document.querySelector("#result");
const drawDisplay = document.querySelector(".draw-display");
const rollingNumber = document.querySelector("#rolling-number");
const rollingName = document.querySelector("#rolling-name");

const fallbackParticipants = [
  {name: "抽奖中", number: 1},
  {name: "抽奖中", number: 8},
  {name: "抽奖中", number: 18},
  {name: "抽奖中", number: 28},
  {name: "抽奖中", number: 58},
];

const sleep = (delay) => new Promise((resolve) => window.setTimeout(resolve, delay));

async function fetchAvailableParticipants() {
  const response = await fetch("/api/participants");
  const body = await response.json();
  if (!body.success || body.data.participants.length === 0) {
    return fallbackParticipants;
  }
  return body.data.participants;
}

function showParticipant(participant) {
  rollingNumber.textContent = String(participant.number).padStart(2, "0");
  rollingName.textContent = participant.name;
}

function startRolling(participants) {
  let index = 0;
  drawDisplay.classList.remove("winner");
  drawDisplay.classList.add("rolling");
  showParticipant(participants[index]);
  return window.setInterval(() => {
    index = (index + 1) % participants.length;
    showParticipant(participants[index]);
  }, 80);
}

async function stopRolling(intervalId, participants, winner) {
  window.clearInterval(intervalId);
  const reel = [...participants, ...participants, ...participants, winner];
  const delays = [70, 80, 90, 110, 130, 160, 190, 230, 280, 340, 420, 520];

  for (let index = 0; index < delays.length; index += 1) {
    showParticipant(reel[index % reel.length]);
    await sleep(delays[index]);
  }

  showParticipant(winner);
  drawDisplay.classList.remove("rolling");
  drawDisplay.classList.add("winner");
}

drawButton.addEventListener("click", async () => {
  drawButton.disabled = true;
  resetButton.disabled = true;
  result.textContent = "抽奖中...";
  let rollingTimer = null;

  try {
    const participants = await fetchAvailableParticipants();
    rollingTimer = startRolling(participants);
    const response = await fetch("/api/draw", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({count: 1, exclude_winners: true}),
    });
    const body = await response.json();
    if (body.success) {
      await stopRolling(rollingTimer, participants, body.data.winner);
      rollingTimer = null;
      result.textContent = `第 ${body.data.draw.round} 轮：${body.data.winner.name}，号码 ${body.data.winner.number}`;
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
  }
});

resetButton.addEventListener("click", async () => {
  if (!window.confirm("确认重置活动数据？")) {
    return;
  }

  resetButton.disabled = true;
  drawButton.disabled = true;
  drawDisplay.classList.remove("rolling", "winner");
  rollingNumber.textContent = "--";
  rollingName.textContent = "等待抽奖";
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
  }
});
