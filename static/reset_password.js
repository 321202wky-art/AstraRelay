(() => {
  const emailInput = document.querySelector('#email');
  const csrfInput = document.querySelector('input[name="_csrf_token"]');
  const sendButton = document.querySelector('#send-reset-code');
  const status = document.querySelector('#reset-code-status');
  let countdownTimer = null;

  function showStatus(message, isError = false) {
    status.hidden = false;
    status.textContent = message;
    status.classList.toggle('login-alert', isError);
    status.classList.toggle('login-notice', !isError);
  }

  function startResetCodeCountdown(seconds) {
    let remaining = Math.max(1, Number(seconds) || 60);
    clearInterval(countdownTimer);
    sendButton.disabled = true;

    const render = () => {
      sendButton.textContent = `${remaining} 秒后重新发送`;
    };
    render();
    countdownTimer = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        sendButton.disabled = false;
        sendButton.textContent = '重新发送验证码';
        return;
      }
      render();
    }, 1000);
  }

  async function requestResetCode() {
    const email = emailInput.value.trim();
    if (!email) {
      showStatus('请先填写邮箱地址。', true);
      emailInput.focus();
      return;
    }

    sendButton.disabled = true;
    try {
      const response = await fetch('/api/auth/password-reset-code', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfInput.value,
        },
        body: JSON.stringify({ email }),
      });
      const payload = await response.json();
      if (!response.ok && response.status !== 429) {
        throw new Error(payload.error || '验证码发送失败，请稍后重试。');
      }
      showStatus(payload.message || payload.error || '请稍后重试。', response.status === 429);
      startResetCodeCountdown(payload.retry_after_seconds);
    } catch (error) {
      showStatus(error.message || '验证码发送失败，请稍后重试。', true);
      sendButton.disabled = false;
      sendButton.textContent = '发送验证码';
    }
  }

  sendButton?.addEventListener('click', requestResetCode);
})();
