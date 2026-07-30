import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createBoundedMessageStore,
  textFromMessageContent,
  reactionTargetText,
} from './bridge_helpers.js';

function sentText(id, text) {
  return { key: { id, remoteJid: '7701@s.whatsapp.net', fromMe: true },
           message: { conversation: text } };
}

test('textFromMessageContent reads plain conversation text', () => {
  assert.equal(textFromMessageContent({ conversation: 'Купила молоко?' }), 'Купила молоко?');
});

test('textFromMessageContent reads extendedTextMessage', () => {
  assert.equal(
    textFromMessageContent({ extendedTextMessage: { text: 'с цитатой' } }),
    'с цитатой',
  );
});

test('textFromMessageContent returns empty string for unknown content', () => {
  assert.equal(textFromMessageContent({ stickerMessage: {} }), '');
  assert.equal(textFromMessageContent(null), '');
});

test('reactionTargetText finds the text of a stored outbound message', () => {
  const store = createBoundedMessageStore(8);
  store.remember(sentText('M1', 'Купила молоко?'));
  assert.equal(reactionTargetText(store, 'M1'), 'Купила молоко?');
});

test('reactionTargetText returns null when the id is unknown', () => {
  const store = createBoundedMessageStore(8);
  assert.equal(reactionTargetText(store, 'GONE'), null);
});

test('reactionTargetText returns null for a stored message with no text', () => {
  const store = createBoundedMessageStore(8);
  store.remember({ key: { id: 'M2' }, message: { stickerMessage: {} } });
  assert.equal(reactionTargetText(store, 'M2'), null);
});

test('reactionTargetText truncates to maxLen', () => {
  const store = createBoundedMessageStore(8);
  store.remember(sentText('M3', 'я'.repeat(900)));
  assert.equal(reactionTargetText(store, 'M3', 500).length, 500);
});

test('reactionTargetText finds any chunk of a split message', () => {
  const store = createBoundedMessageStore(8);
  store.remember(sentText('C1', 'первый кусок'));
  store.remember(sentText('C2', 'второй кусок'));
  assert.equal(reactionTargetText(store, 'C1'), 'первый кусок');
  assert.equal(reactionTargetText(store, 'C2'), 'второй кусок');
});

test('reactionTargetText returns null after the entry is evicted', () => {
  const store = createBoundedMessageStore(2);
  store.remember(sentText('E1', 'старое'));
  store.remember(sentText('E2', 'среднее'));
  store.remember(sentText('E3', 'новое'));
  assert.equal(reactionTargetText(store, 'E1'), null);
  assert.equal(reactionTargetText(store, 'E3'), 'новое');
});
