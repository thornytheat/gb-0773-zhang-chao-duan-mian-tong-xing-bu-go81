const sample = document.querySelector('#sample');
sample?.addEventListener('change', () => document.querySelector('input[name=run]')?.remove());
document.querySelector('#print-book')?.addEventListener('click', () => window.print());
const rerun = document.querySelector('#rerun');
rerun?.addEventListener('click', async () => {
  const feedback = document.querySelector('#feedback');
  rerun.disabled = true;
  feedback.textContent = '复算中…';
  try {
    const res = await fetch(`/api/journal/${rerun.dataset.run}/rerun`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || '复算失败');
    feedback.textContent = data.reused ? '复算一致，沿用原簿' : '新簿已归档';
  } catch (error) {
    feedback.textContent = `${error.message}，请重试。`;
  } finally { rerun.disabled = false; }
});

const signoff = document.querySelector('.sign');
signoff?.addEventListener('click', async () => {
  const feedback = document.querySelector('#permit-feedback');
  signoff.disabled = true;
  feedback.textContent = '签发中…';
  try {
    const res = await fetch(`/api/journal/${signoff.dataset.run}/permit`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({vessel: signoff.dataset.vessel, policy: signoff.dataset.policy}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || '签发失败');
    feedback.textContent = data.reused
      ? `同一补正已签过，沿用凭据 ${data.permit_id.slice(0, 12)}`
      : `凭据 ${data.permit_id.slice(0, 12)} 已签发`;
    if (data.permit_id) {
      const list = document.querySelector('#permit-list');
      const item = document.createElement('li');
      item.textContent = `${data.permit_id.slice(0, 12)} · ${data.permit.vessel} · ${data.permit.decision.cleared_s} s`;
      list.append(item);
    }
  } catch (error) {
    feedback.textContent = `${error.message}，请核对取舍后重试。`;
  } finally { signoff.disabled = false; }
});
