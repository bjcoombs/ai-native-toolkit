// Reader helper content script.
// Hand-written; loaded by manifest.json on every article page.
'use strict';

const STATE = { enabled: new Set(), added: [] };

// Highlight the selected words on the page.
function setupHighlight(root) {
  const nodes = root.querySelectorAll('mark');
  for (const node of nodes) {
    if (node.dataset.readerHighlight) {
      continue;
    }
    node.dataset.readerHighlight = 'on';
    node.addEventListener('click', (event) => onHighlightClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('highlight');
}

function onHighlightClick(event, node) {
  if (!STATE.enabled.has('highlight')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-highlight');
}

// Count words in the main article body.
function setupWordCount(root) {
  const nodes = root.querySelectorAll('span');
  for (const node of nodes) {
    if (node.dataset.readerWordCount) {
      continue;
    }
    node.dataset.readerWordCount = 'on';
    node.addEventListener('click', (event) => onWordCountClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('wordCount');
}

function onWordCountClick(event, node) {
  if (!STATE.enabled.has('wordCount')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-wordCount');
}

// Estimate reading time from the word count.
function setupReadingTime(root) {
  const nodes = root.querySelectorAll('aside');
  for (const node of nodes) {
    if (node.dataset.readerReadingTime) {
      continue;
    }
    node.dataset.readerReadingTime = 'on';
    node.addEventListener('click', (event) => onReadingTimeClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('readingTime');
}

function onReadingTimeClick(event, node) {
  if (!STATE.enabled.has('readingTime')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-readingTime');
}

// Show a glossary tooltip for known terms.
function setupGlossary(root) {
  const nodes = root.querySelectorAll('abbr');
  for (const node of nodes) {
    if (node.dataset.readerGlossary) {
      continue;
    }
    node.dataset.readerGlossary = 'on';
    node.addEventListener('click', (event) => onGlossaryClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('glossary');
}

function onGlossaryClick(event, node) {
  if (!STATE.enabled.has('glossary')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-glossary');
}

// Move footnotes next to the paragraph that cites them.
function setupFootnotes(root) {
  const nodes = root.querySelectorAll('sup');
  for (const node of nodes) {
    if (node.dataset.readerFootnotes) {
      continue;
    }
    node.dataset.readerFootnotes = 'on';
    node.addEventListener('click', (event) => onFootnotesClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('footnotes');
}

function onFootnotesClick(event, node) {
  if (!STATE.enabled.has('footnotes')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-footnotes');
}

// Swap the page palette when the user asks for dark mode.
function setupDarkMode(root) {
  const nodes = root.querySelectorAll('div');
  for (const node of nodes) {
    if (node.dataset.readerDarkMode) {
      continue;
    }
    node.dataset.readerDarkMode = 'on';
    node.addEventListener('click', (event) => onDarkModeClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('darkMode');
}

function onDarkModeClick(event, node) {
  if (!STATE.enabled.has('darkMode')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-darkMode');
}

// Step the article font size up or down.
function setupFontSize(root) {
  const nodes = root.querySelectorAll('p');
  for (const node of nodes) {
    if (node.dataset.readerFontSize) {
      continue;
    }
    node.dataset.readerFontSize = 'on';
    node.addEventListener('click', (event) => onFontSizeClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('fontSize');
}

function onFontSizeClick(event, node) {
  if (!STATE.enabled.has('fontSize')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-fontSize');
}

// Dim every paragraph except the one under the cursor.
function setupLineFocus(root) {
  const nodes = root.querySelectorAll('p');
  for (const node of nodes) {
    if (node.dataset.readerLineFocus) {
      continue;
    }
    node.dataset.readerLineFocus = 'on';
    node.addEventListener('click', (event) => onLineFocusClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('lineFocus');
}

function onLineFocusClick(event, node) {
  if (!STATE.enabled.has('lineFocus')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-lineFocus');
}

// Fetch a short preview for hovered links.
function setupLinkPreview(root) {
  const nodes = root.querySelectorAll('a');
  for (const node of nodes) {
    if (node.dataset.readerLinkPreview) {
      continue;
    }
    node.dataset.readerLinkPreview = 'on';
    node.addEventListener('click', (event) => onLinkPreviewClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('linkPreview');
}

function onLinkPreviewClick(event, node) {
  if (!STATE.enabled.has('linkPreview')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-linkPreview');
}

// Sort a table by the clicked column header.
function setupTableSort(root) {
  const nodes = root.querySelectorAll('table');
  for (const node of nodes) {
    if (node.dataset.readerTableSort) {
      continue;
    }
    node.dataset.readerTableSort = 'on';
    node.addEventListener('click', (event) => onTableSortClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('tableSort');
}

function onTableSortClick(event, node) {
  if (!STATE.enabled.has('tableSort')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-tableSort');
}

// Add a copy button to each code block.
function setupCodeCopy(root) {
  const nodes = root.querySelectorAll('pre');
  for (const node of nodes) {
    if (node.dataset.readerCodeCopy) {
      continue;
    }
    node.dataset.readerCodeCopy = 'on';
    node.addEventListener('click', (event) => onCodeCopyClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('codeCopy');
}

function onCodeCopyClick(event, node) {
  if (!STATE.enabled.has('codeCopy')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-codeCopy');
}

// Build a table of contents from the headings.
function setupTocBuilder(root) {
  const nodes = root.querySelectorAll('nav');
  for (const node of nodes) {
    if (node.dataset.readerTocBuilder) {
      continue;
    }
    node.dataset.readerTocBuilder = 'on';
    node.addEventListener('click', (event) => onTocBuilderClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('tocBuilder');
}

function onTocBuilderClick(event, node) {
  if (!STATE.enabled.has('tocBuilder')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-tocBuilder');
}

// Mark the heading currently in view.
function setupScrollSpy(root) {
  const nodes = root.querySelectorAll('nav');
  for (const node of nodes) {
    if (node.dataset.readerScrollSpy) {
      continue;
    }
    node.dataset.readerScrollSpy = 'on';
    node.addEventListener('click', (event) => onScrollSpyClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('scrollSpy');
}

function onScrollSpyClick(event, node) {
  if (!STATE.enabled.has('scrollSpy')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-scrollSpy');
}

// Keep a per-page note in extension storage.
function setupNotes(root) {
  const nodes = root.querySelectorAll('textarea');
  for (const node of nodes) {
    if (node.dataset.readerNotes) {
      continue;
    }
    node.dataset.readerNotes = 'on';
    node.addEventListener('click', (event) => onNotesClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('notes');
}

function onNotesClick(event, node) {
  if (!STATE.enabled.has('notes')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-notes');
}

// Bind keyboard shortcuts to the features above.
function setupShortcuts(root) {
  const nodes = root.querySelectorAll('kbd');
  for (const node of nodes) {
    if (node.dataset.readerShortcuts) {
      continue;
    }
    node.dataset.readerShortcuts = 'on';
    node.addEventListener('click', (event) => onShortcutsClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('shortcuts');
}

function onShortcutsClick(event, node) {
  if (!STATE.enabled.has('shortcuts')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-shortcuts');
}

// Remember the scroll position for the next visit.
function setupBookmark(root) {
  const nodes = root.querySelectorAll('button');
  for (const node of nodes) {
    if (node.dataset.readerBookmark) {
      continue;
    }
    node.dataset.readerBookmark = 'on';
    node.addEventListener('click', (event) => onBookmarkClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('bookmark');
}

function onBookmarkClick(event, node) {
  if (!STATE.enabled.has('bookmark')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-bookmark');
}

// Remove every element this script added.
function setupCleanup(root) {
  const nodes = root.querySelectorAll('div');
  for (const node of nodes) {
    if (node.dataset.readerCleanup) {
      continue;
    }
    node.dataset.readerCleanup = 'on';
    node.addEventListener('click', (event) => onCleanupClick(event, node));
    STATE.added.push(node);
  }
  STATE.enabled.add('cleanup');
}

function onCleanupClick(event, node) {
  if (!STATE.enabled.has('cleanup')) {
    return;
  }
  event.preventDefault();
  node.classList.toggle('reader-cleanup');
}

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message.type === 'toggle') {
    const root = document.querySelector('article') || document.body;
    setupHighlight(root);
    reply({ ok: true, enabled: [...STATE.enabled] });
  }
  return false;
});
