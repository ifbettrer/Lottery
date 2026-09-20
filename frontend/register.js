const form = document.querySelector("#register-form");
const button = document.querySelector("#submit-button");
const result = document.querySelector("#result");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  result.textContent = "提交中...";
  const name = document.querySelector("#name").value;

  try {
    const response = await fetch("/api/register", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    const body = await response.json();
    if (body.success) {
      result.textContent = `取号成功：${body.data.name}，号码 ${body.data.number}`;
    } else {
      result.textContent = body.error.message;
    }
  } catch (_) {
    result.textContent = "系统繁忙，请稍后重试";
  } finally {
    button.disabled = false;
  }
});
