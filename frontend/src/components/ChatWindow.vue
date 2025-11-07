<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { marked } from "marked";

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: "" },
  selectedCharacter: { type: Object, default: null },
  config: { type: Object, default: () => ({}) },
  extractKeywords: { type: Function, required: true },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits(["close"]);

const messagesContainer = ref(null);
const inputField = ref(null);

const chatMessages = ref([]);
const chatInput = ref("");
const chatLoading = ref(false);

const inputModel = computed({
  get: () => chatInput.value,
  set: (value) => {
    chatInput.value = value ?? "";
  },
});

function renderMarkdown(text) {
  return marked.parse(text || "");
}

function focusInput() {
  if (inputField.value) {
    inputField.value.focus();
  }
}

function scrollToBottom() {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  }
}

function handleClose() {
  emit("close");
}

function handleSubmit() {
  sendChatMessageAndFocus();
}

function handleTextareaKeydown(event) {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey
  ) {
    event.preventDefault();
    sendChatMessageAndFocus();
  }
}

function handleImageLoad() {
  nextTick(scrollToBottom);
}

watch(
  () => chatMessages.value.length,
  () => {
    if (!props.open) return;
    nextTick(scrollToBottom);
  }
);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      nextTick(() => {
        scrollToBottom();
        focusInput();
      });
    } else {
      chatInput.value = "";
      chatLoading.value = false;
    }
  }
);

async function sendChatMessageAndFocus() {
  const input = chatInput.value.trim();
  if (!input || chatLoading.value) return;

  const character = props.selectedCharacter;
  let systemMessage =
    "You should always respond as the character you are playing. Stay in character and don't break it. Let me speak for myself. Do not repeat yourself.";

  if (chatMessages.value.length === 0) {
    if (character && typeof character.name === "string" && character.name) {
      systemMessage += ` You are now assuming the role of the character named '${character.name}'.`;
      if (
        character.description &&
        typeof character.description === "string" &&
        character.description.trim().length > 0
      ) {
        systemMessage += ` Here is some information about you: ${character.description.trim()}`;
      }
    } else {
      systemMessage +=
        " You are now assuming the role of a generic character without a specific name or background.";
    }
    chatMessages.value.push(
      { role: "user", content: input },
      { role: "system", content: systemMessage }
    );
  } else {
    chatMessages.value.push({ role: "user", content: input });
  }

  chatInput.value = "";
  chatLoading.value = true;
  await nextTick();
  scrollToBottom();

  try {
    const host = props.config?.openai_host ?? "localhost";
    const port = props.config?.openai_port ?? 8000;
    const model = props.config?.openai_model ?? "gpt-3.5-turbo";
    const url = `http://${host}:${port}/v1/chat/completions`;

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: chatMessages.value.map((m) => ({
          role: m.role,
          content: m.content,
        })),
        stream: false,
      }),
    });

    if (!res.ok) throw new Error("OpenAI server error");
    const data = await res.json();
    const reply = data.choices?.[0]?.message?.content || "(No response)";

    chatMessages.value.push({ role: "assistant", content: reply });
    await nextTick();
    scrollToBottom();

    let lastUser = null;
    for (let i = chatMessages.value.length - 2; i >= 0; i--) {
      if (chatMessages.value[i].role === "user") {
        lastUser = chatMessages.value[i].content;
        break;
      }
    }

    const keywordFn =
      typeof props.extractKeywords === "function"
        ? props.extractKeywords
        : (text) => text;

    let searchQuery = keywordFn(reply);
    if (lastUser) {
      searchQuery = `${lastUser} ${searchQuery}`;
    }
    if (character && character.name) {
      searchQuery = `${character.name} ${searchQuery}`;
    }

    if (props.backendUrl) {
      try {
        const searchRes = await fetch(
          `${props.backendUrl}/search?query=${encodeURIComponent(searchQuery)}`
        );
        if (searchRes.ok) {
          const searchData = await searchRes.json();
          if (Array.isArray(searchData) && searchData.length > 0) {
            const totalScore = searchData.reduce(
              (sum, pic) => sum + (pic.likeness_score || 0),
              0
            );
            let r = Math.random() * (totalScore || 1);
            let chosen = searchData[0];
            for (const pic of searchData) {
              r -= pic.likeness_score || 0;
              if (r <= 0) {
                chosen = pic;
                break;
              }
            }
            const imageUrl = `${props.backendUrl}/pictures/${chosen.id}`;
            for (let i = chatMessages.value.length - 1; i >= 0; i--) {
              const msg = chatMessages.value[i];
              if (msg.role === "assistant" && !msg.pictureUrl) {
                chatMessages.value[i] = { ...msg, pictureUrl: imageUrl };
                break;
              }
            }
          }
        }
      } catch (error) {
        // Ignore search errors for now.
      }
    }
  } catch (error) {
    chatMessages.value.push({
      role: "assistant",
      content: "Error: " + (error.message || error),
    });
  } finally {
    chatLoading.value = false;
    await nextTick();
    focusInput();
  }
}

defineExpose({ focusInput, scrollToBottom });
</script>

<template>
  <div v-if="open" class="chat-overlay" @click.self="handleClose">
    <div class="chat-overlay-content">
      <div class="chat-overlay-header">
        <span>{{ title }}</span>
        <button class="overlay-close" @click="handleClose" aria-label="Close">
          &times;
        </button>
      </div>
      <div class="overlay-chat-main">
        <div class="chat-messages" ref="messagesContainer">
          <div
            v-for="(msg, i) in chatMessages"
            :key="i"
            :class="
              msg.role === 'user'
                ? 'chat-message-user'
                : 'chat-message-assistant'
            "
          >
            <template v-if="msg.role === 'user'">
              <div class="chat-bubble user">
                <span class="chat-username">You</span>
                <span class="chat-text">{{ msg.content }}</span>
              </div>
            </template>
            <template v-else>
              <div class="chat-assistant-full">
                <span class="chat-username">AI</span>
                <span
                  class="chat-text"
                  v-html="renderMarkdown(msg.content)"
                ></span>
                <div v-if="msg.pictureUrl" class="chat-picture-result">
                  <img
                    :src="msg.pictureUrl"
                    alt="result"
                    @load="handleImageLoad"
                  />
                </div>
              </div>
            </template>
          </div>
          <div v-if="chatLoading" class="chat-message-assistant">
            <div class="chat-assistant-full">
              <span class="chat-username">AI</span>
              <span class="chat-text">...</span>
            </div>
          </div>
        </div>
        <form class="chat-input-row" @submit.prevent="handleSubmit">
          <textarea
            v-model="inputModel"
            class="chat-input"
            placeholder="Type your message..."
            rows="2"
            :disabled="chatLoading"
            ref="inputField"
            @keydown="handleTextareaKeydown"
          ></textarea>
          <v-btn
            type="submit"
            :disabled="!inputModel.trim() || chatLoading"
            color="primary"
            class="chat-send-btn"
          >
            <v-icon>mdi-send</v-icon>
          </v-btn>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.55);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-overlay-content {
  background: #fff;
  width: 90vw;
  height: 90vh;
  border-radius: 18px;
  box-shadow: 0 4px 32px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-overlay-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.2em 1.5em;
  background: #29405a;
  color: #fff;
  font-size: 1.3em;
  font-weight: 600;
  position: relative;
}

.chat-overlay-header span {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  font-size: 1.13em;
  font-weight: 600;
  padding-right: 0.5em;
}

.chat-overlay-header .overlay-close {
  position: absolute;
  top: 0.5em;
  right: 0.7em;
  font-size: 2em;
  color: #fff;
  background: transparent;
  border: none;
  cursor: pointer;
  line-height: 1;
  padding: 0 4px;
  transition: color 0.2s;
}

.chat-overlay-header .overlay-close:hover {
  color: #ff5252;
}

.overlay-chat-main {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: stretch;
}

.chat-messages {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 100%;
  overflow-y: auto;
  padding: 1.2em 1.5em 1em 1.5em;
  background: #f7f7fa;
  border-radius: 12px;
  margin-bottom: 1em;
  display: flex;
  flex-direction: column;
  gap: 0.7em;
}

.chat-message-user,
.chat-message-assistant {
  display: flex;
  margin-bottom: 0.7em;
}

.chat-message-user {
  justify-content: flex-end;
}

.chat-message-assistant {
  justify-content: flex-start;
}

.chat-bubble {
  max-width: 0%;
  padding: 0.7em 1.1em;
  border-radius: 18px;
  font-size: 1.08em;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  background: #fff;
  color: #222;
  word-break: break-word;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.chat-bubble.user {
  background: #e3f2fd;
  color: #1976d2;
  border-radius: 18px;
  padding: 0.7em 1.1em;
  max-width: 90%;
  align-self: flex-end;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.chat-assistant-full {
  width: 100%;
  background: none;
  color: #222;
  border-radius: 0;
  padding: 0.2em 0;
  box-shadow: none;
  font-size: 1.08em;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.4em;
}

.chat-username {
  font-size: 0.92em;
  font-weight: 600;
  margin-bottom: 0.2em;
  opacity: 0.7;
}

.chat-text {
  white-space: pre-line;
  word-break: break-word;
}

.chat-picture-result {
  margin-top: 0.5em;
}

.chat-picture-result img {
  max-width: 50%;
  height: auto;
  display: block;
  margin: 0 auto;
  border-radius: 8px;
  box-shadow: 0 2px 8px #0002;
}

.chat-input-row {
  flex-shrink: 0;
  background: #f8fafd;
  border-top: 1px solid #e0e0e0;
  padding: 0.5em 0.7em;
  display: flex;
  align-items: flex-end;
  gap: 0.5em;
  margin-bottom: 10px;
}

.chat-input {
  flex: 1;
  min-height: 2.5em;
  max-height: 7em;
  resize: none;
  border-radius: 6px;
  border: 1px solid #d0d0d0;
  padding: 0.9em 1.2em;
  font-size: 1.05em;
  outline: none;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.chat-send-btn {
  min-width: 48px;
  min-height: 48px;
  border-radius: 50%;
  background: #1976d2;
  color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transition: background 0.2s;
}

.chat-send-btn:disabled {
  background: #bbb;
  color: #fff;
  cursor: not-allowed;
}

@media (max-width: 900px) {
  .chat-overlay-content {
    width: 98vw;
    height: 95vh;
  }

  .chat-picture-result img {
    max-width: 70%;
  }
}
</style>
