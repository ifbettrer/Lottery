const drawButton = document.querySelector("#draw-button");
const resetButton = document.querySelector("#reset-button");
const result = document.querySelector("#result");

drawButton.addEventListener("click", async () => {
  drawButton.disabled = true;
  result.textContent = "抽奖中...";

  try {
    const response = await fetch("/api/draw", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({count: 1, exclude_winners: true}),
    });
    const body = await response.json();
    if (body.success) {
      result.textContent = `第 ${body.data.draw.round} 轮：${body.data.winner.name}，号码 ${body.data.winner.number}`;
    } else {
      result.textContent = body.error.message;
    }
  } catch (_) {
    result.textContent = "系统繁忙，请稍后重试";
  } finally {
    drawButton.disabled = false;
  }
});

resetButton.addEventListener("click", async () => {
  if (!window.confirm("确认重置活动数据？")) {
    return;
  }

  resetButton.disabled = true;
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
  }
});
