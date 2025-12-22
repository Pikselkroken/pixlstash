import { ref } from "vue";

const visible = ref(false);

export function useSearchOverlay() {
  function openSearchOverlay() {
    visible.value = true;
  }

  function closeSearchOverlay() {
    visible.value = false;
  }

  return {
    visible,
    openSearchOverlay,
    closeSearchOverlay,
  };
}