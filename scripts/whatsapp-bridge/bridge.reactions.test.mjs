import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createBoundedMessageStore,
  textFromMessageContent,
  reactionTargetText,
  reactionDedupeKey,
  normalizeReactionTimestampMs,
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


// -- reactionDedupeKey: see followup-brief.md Fix 2 --------------------
// The dedupe key must identify the reaction (target+sender+emoji+when),
// not just its content, so a genuine remove-then-reapply of the same
// emoji is not swallowed as if it were a WhatsApp-redelivered duplicate.

function baseReactionEvent(overrides = {}) {
  return {
    targetMessageId: 'M1',
    senderId: '77011102626@s.whatsapp.net',
    emoji: '👍',
    ...overrides,
  };
}

test('reactionDedupeKey: same target/sender/emoji/timestamp -> identical key (redelivery dedupes)', () => {
  const a = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: 1690000000000 }));
  const b = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: 1690000000000 }));
  assert.equal(a, b);
});

test('reactionDedupeKey: same target/sender/emoji, different timestamp -> different key (re-add gets through)', () => {
  const a = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: 1690000000000 }));
  const b = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: 1690000000123 }));
  assert.notEqual(a, b);
});

test('reactionDedupeKey: timestamp absent -> key equals the legacy format exactly', () => {
  const key = reactionDedupeKey(baseReactionEvent());
  assert.equal(key, 'react:M1:77011102626@s.whatsapp.net:👍');
});

test('reactionDedupeKey: Long-style object and equal-value number produce the same key', () => {
  const longLike = { toString: () => '1690000000000' };
  const asNumber = 1690000000000;
  const a = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: longLike }));
  const b = reactionDedupeKey(baseReactionEvent({ senderTimestampMs: asNumber }));
  assert.equal(a, b);
});

test('normalizeReactionTimestampMs: number, Long-like object, and absent are each handled', () => {
  assert.equal(normalizeReactionTimestampMs(42), '42');
  assert.equal(normalizeReactionTimestampMs({ toString: () => '42' }), '42');
  assert.equal(normalizeReactionTimestampMs(undefined), undefined);
  assert.equal(normalizeReactionTimestampMs(null), undefined);
});
