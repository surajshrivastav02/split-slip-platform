const el = id => document.getElementById(id);
let challengeToken = '';
let questionVersion = 0;

async function send(action, body) {
  const response = await fetch('/api/slip?action=' + action, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
const contentType = response.headers.get('content-type') || '';
  if (!response.ok) throw new Error(contentType.includes('application/json') ? (await response.json()).error || 'Please try again.' : 'Please try again.');
  if (!contentType.includes('application/json')) throw new Error('Please try again.');
  return await response.json();
}

async function newQuestion() {
  const version = ++questionVersion;
  challengeToken = '';
  el('answer').value = '';
  el('submit').disabled = true;
  el('question').textContent = 'Loading…';
  try {
    const result = await send('challenge', {});
    if (version !== questionVersion) return;
    challengeToken = result.challenge;
    el('question').textContent = result.question;
    el('submit').disabled = false;
  } catch (error) {
    if (version !== questionVersion) return;
    el('question').textContent = 'Use New question to retry.';
    el('message').textContent = error.message;
  }
}

function mode() {
  const signup = location.hash === '#signup';
  el('nameRow').hidden = el('upiRow').hidden = !signup;
  el('fullName').disabled = el('upi').disabled = !signup;
  el('fullName').required = signup;
  el('password').minLength = signup ? 8 : 1;
  el('password').autocomplete = signup ? 'new-password' : 'current-password';
  el('password').value = '';
  el('heading').textContent = signup ? 'Join Split Slip' : 'Welcome back';
  el('submit').textContent = signup ? 'Create account' : 'Log in';
  el('message').textContent = '';
  newQuestion();
}
el('refresh').onclick = newQuestion;
window.addEventListener('hashchange', mode);
el('account').onsubmit = async event => {
  event.preventDefault();
  el('submit').disabled = true;
  try {
    await send(location.hash === '#signup' ? 'signup' : 'login', {
      name: el('fullName').value, phone: el('phone').value, upi: el('upi').value,
      password: el('password').value, answer: el('answer').value.trim(), challenge: challengeToken
    });
    location.replace('/dashboard.htm');
  } catch (error) {
    el('message').textContent = error.message;
    await newQuestion();
  }
};
mode();
