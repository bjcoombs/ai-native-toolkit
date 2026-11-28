// Popup for the reader helper.
'use strict';

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

document.getElementById('toggle').addEventListener('click', async () => {
  const tab = await activeTab();
  const reply = await chrome.tabs.sendMessage(tab.id, { type: 'toggle' });
  document.getElementById('status').textContent = reply.ok ? 'On' : 'Off';
});
