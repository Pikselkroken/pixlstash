<template>
  <input
    type="number"
    :value="value"
    @change="emit('change', $event.target.value, $event.target)"
    @keydown.enter.stop="$event.target.blur()"
  />
</template>

<script setup>
// A LoRA row's strength field on the Workflow tab, as a component of its own
// so that it keeps what is being typed in it.
//
// Vue writes a bound `value` back to an input on every render of the template
// it sits in, changed or not. Drawn in the tab's own template, the field was
// put back to the row's number by every answer that landed while a strength
// was half typed (a list refetch, the pre-flight, the replacement reads), and
// Enter then changed nothing. A component is rendered again only when what it
// is given changes: a new number for the row, or the lock that disables it,
// which is when the field is meant to say the row's number again.
//
// The class, the range, `disabled` and the accessible name fall through to
// the input.
//
// Enter commits: it blurs the field, which is the native change. Inside an
// AppDialog the key would otherwise be taken as "accept" (a dialog with no
// accept listener) and the typed value would never be sent.

defineProps({
  /** The row's strength as the field shows it. */
  value: { type: String, required: true },
});

// Declared, so the tab's per-row handler is not a reason to render again.
const emit = defineEmits(["change"]);
</script>
