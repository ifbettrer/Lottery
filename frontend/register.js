const form = document.querySelector("#register-form");
const button = document.querySelector("#submit-button");
const result = document.querySelector("#result");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  result.textContent = "提交中...";
  const name = document.querySelector("#name").value;
  const phone = document.querySelector("#phone").value.trim();

  if (phone && !/^\d+$/.test(phone)) {
    result.textContent = "手机号不能为空或格式不正确";
    button.disabled = false;
    return;
  }
  if (phone.length < 11) {
    result.textContent = "电话号码不足11位";
    button.disabled = false;
    return;
  }
  if (phone.length !== 11) {
    result.textContent = "手机号不能为空或格式不正确";
    button.disabled = false;
    return;
  }

  try {
    const response = await fetch("/api/register", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, phone}),
    });
    const body = await response.json();
    if (body.success) {
      result.textContent = `取号成功：${body.data.name}，号码 ${body.data.number}`;
    } else if (body.error.code === "DUPLICATE_PHONE" && body.data?.number) {
      result.textContent = `${body.error.message}，您之前取到的号码是 ${body.data.number}`;
    } else {
      result.textContent = body.error.message;
    }
  } catch (_) {
    result.textContent = "系统繁忙，请稍后重试";
  } finally {
    button.disabled = false;
  }
});
