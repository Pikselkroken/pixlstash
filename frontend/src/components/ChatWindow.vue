<script setup>
import {
  computed,
  nextTick,
  ref,
  watch,
  onMounted,
  onBeforeUnmount,
} from "vue";
import nlp from "compromise";
// FIFO queue for last 20 displayed pictures
import { marked } from "marked";

const props = defineProps({
  open: { type: Boolean, default: false },
  selectedCharacter: { type: String, default: null },
  config: { type: Object, default: () => ({}) },
  backendUrl: { type: String, required: true },
});

// Conversation state
const conversationId = ref(null); // integer conversation_id from backend

const displayedPictureQueue = ref([]);

const selectedCharacterObj = ref(null);

function handleGlobalKeydown(e) {
  if (e.key === "Escape" && props.open) {
    handleClose();
  }
}

// Get or create a conversation for the selected character
async function ensureConversation() {
  if (!props.backendUrl || !props.selectedCharacter) return null;
  try {
    const res = await fetch(`${props.backendUrl}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: props.selectedCharacter }),
    });
    if (!res.ok) throw new Error("Failed to get/create conversation");
    const data = await res.json();
    conversationId.value = data.conversation_id;
    return data.conversation_id;
  } catch (e) {
    conversationId.value = null;
    return null;
  }
}

async function loadChatHistory() {
  if (!props.backendUrl || !props.selectedCharacter) return;
  const convId = await ensureConversation();
  if (!convId) return;
  try {
    const res = await fetch(
      `${props.backendUrl}/conversations/${convId}/messages?limit=100`
    );
    if (res.ok) {
      const data = await res.json();
      let messages = Array.isArray(data.messages) ? data.messages : [];
      messages = messages.map((msg) => {
        if (msg.picture_id) {
          const pictureUrl = `${props.backendUrl}/pictures/${msg.picture_id}`;
          return { ...msg, pictureUrl };
        }
        return msg;
      });
      chatMessages.value = messages;
    }
  } catch (e) {
    // ignore
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      window.addEventListener("keydown", handleGlobalKeydown);
      loadChatHistory();
    } else {
      window.removeEventListener("keydown", handleGlobalKeydown);
    }
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleGlobalKeydown);
});

watch(
  () => props.selectedCharacter,
  async (newId) => {
    if (newId) {
      selectedCharacterObj.value = await fetchCharacterById(newId);
    } else {
      selectedCharacterObj.value = null;
    }
  },
  { immediate: true }
);

const title = computed(() => {
  if (selectedCharacterObj.value && selectedCharacterObj.value.name) {
    return `Chat with ${selectedCharacterObj.value.name}`;
  }
  return "AI Chat";
});

function extractKeywords(text) {
  const doc = nlp(text);
  const nouns = doc.nouns().out("array");
  const adjectives = doc.adjectives().out("array");
  const keywords = Array.from(new Set([...nouns, ...adjectives]));
  return keywords.join(" ");
}

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
        focusInput();
      });
    } else {
      chatInput.value = "";
      chatLoading.value = false;
    }
  }
);

async function fetchCharacterById(characterId) {
  if (!props.backendUrl || !characterId) return null;
  try {
    const res = await fetch(
      `${props.backendUrl}/characters/${encodeURIComponent(characterId)}`
    );
    if (!res.ok) throw new Error("Failed to fetch character");
    const data = await res.json();
    console.log("Fetched character data:", data);
    return data;
  } catch (error) {
    console.error("Error fetching character:", error);
    return null;
  }
}

async function sendChatMessageAndFocus() {
  const input = chatInput.value.trim();
  if (!input || chatLoading.value) return;

  // Ensure conversation exists and get its id
  const convId = await ensureConversation();
  if (!convId) return;

  const characterId = props.selectedCharacter;
  const character = await fetchCharacterById(characterId);

  let systemMessage =
    "You should always respond as the character you are playing. Stay in character and don't break it. Let me speak for myself. Do not repeat yourself.\n\nIMPORTANT: Your response must have TWO parts:\n1. First, your normal character dialogue/response\n2. Then on a new line at the END, add: [SEARCH: character at location in outfit, mood, action]\n\nExample format:\n*giggles and points at the ocean* Look at that!\n[SEARCH: Clementine at beach in white bikini, smiling, pointing at ocean]\n\nThe search line should be concise (under 50 words) with visual details: character name, location, clothing, mood, and current action.";

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
    // Save both user and system messages
    await saveChatMessage({ role: "user", content: input });
    await saveChatMessage({ role: "system", content: systemMessage });
  } else {
    chatMessages.value.push({ role: "user", content: input });
    await saveChatMessage({ role: "user", content: input });
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

    // Prepare messages, adding search reminder every 3 exchanges
    const messagesToSend = chatMessages.value.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // Count user messages (exchanges)
    const userMsgCount = chatMessages.value.filter(
      (m) => m.role === "user"
    ).length;

    // Add search reminder every 3 exchanges
    if (userMsgCount > 0 && userMsgCount % 3 === 0) {
      messagesToSend.push({
        role: "system",
        content:
          "Remember: End your response with [SEARCH: character at location in outfit, mood, action]",
      });
    }

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: messagesToSend,
        stream: false,
      }),
    });

    if (!res.ok) throw new Error("OpenAI server error");
    const data = await res.json();
    const reply = data.choices?.[0]?.message?.content || "(No response)";

    console.log("Full AI response:", reply);

    // Extract search query if present in format [SEARCH: ...]
    let searchQuery = "";
    const searchMatch = reply.match(/\[SEARCH:\s*(.+?)\]/i);
    if (searchMatch) {
      searchQuery = searchMatch[1].trim();
      console.log("AI-generated search query:", searchQuery);
    } else {
      console.log("No [SEARCH: ...] tag found in response");
    }

    chatMessages.value.push({ role: "assistant", content: reply });
    await nextTick();
    scrollToBottom();

    // Fallback: if no search query was extracted, use the reply text
    if (!searchQuery) {
      searchQuery = extractKeywords(reply);

      // Add character name for better matching
      if (character && character.name) {
        searchQuery = `${character.name} ${searchQuery}`;
      }
    }

    if (props.backendUrl) {
      try {
        const url = `${
          props.backendUrl
        }/pictures/search?query=${encodeURIComponent(searchQuery)}&top_n=50`;
        const searchRes = await fetch(url);
        if (searchRes.ok) {
          const searchData = await searchRes.json();
          console.log("Search query:", searchQuery);
          if (Array.isArray(searchData) && searchData.length > 0) {
            // Filter out pictures in the FIFO queue
            const filteredResults = searchData.filter(
              (pic) => !displayedPictureQueue.value.includes(pic.id)
            );
            if (filteredResults.length === 0) {
              chatMessages.value.push({
                role: "system",
                content: `🔍 All top results have already been shown in chat.`,
                isDebug: true,
              });
              await nextTick();
              scrollToBottom();
              return;
            }
            // Always use the best result (highest likeness_score) not in FIFO
            const bestResult = filteredResults[0];
            // Get top 3 results for display (not in FIFO)
            const top3Results = filteredResults.slice(0, 3);
            console.log(
              "Search results (top 3, not in FIFO):",
              top3Results.map((r) => ({
                id: r.id,
                score: r.likeness_score,
                description: r.description,
              }))
            );
            // Build compact debug info with descriptions
            let debugInfo = `🔍 ${filteredResults.length} results | Selected: #1\n\n`;
            for (let i = 0; i < top3Results.length; i++) {
              const pic = top3Results[i];
              const score = (pic.likeness_score * 100).toFixed(0);
              const desc = pic.description || "No description";
              debugInfo += `${i + 1}. ${score}% - ${desc}\n`;
            }
            // Add debug info as a system message with top 3 picture IDs
            chatMessages.value.push({
              role: "system",
              content: debugInfo,
              isDebug: true,
              searchResults: top3Results.map((r) => ({
                id: r.id,
                score: r.likeness_score,
              })),
            });
            // Add bestResult.id to FIFO queue
            displayedPictureQueue.value.push(bestResult.id);
            if (displayedPictureQueue.value.length > 20) {
              displayedPictureQueue.value.shift(); // Remove oldest
            }
            const imageUrl = `${props.backendUrl}/pictures/${bestResult.id}`;
            let assistantMsgIdx = -1;
            for (let i = chatMessages.value.length - 1; i >= 0; i--) {
              const msg = chatMessages.value[i];
              if (msg.role === "assistant" && !msg.pictureUrl && !msg.isDebug) {
                chatMessages.value[i] = { ...msg, pictureUrl: imageUrl };
                assistantMsgIdx = i;
                break;
              }
            }
            // Save the assistant message with picture_id if found, else fallback
            if (assistantMsgIdx !== -1) {
              const msg = chatMessages.value[assistantMsgIdx];
              await saveChatMessage({
                role: msg.role,
                content: msg.content,
                picture_id: bestResult.id,
              });
            } else {
              await saveChatMessage({
                role: "assistant",
                content: reply,
                picture_id: bestResult.id,
              });
            }

            // Scroll after adding debug and image
            await nextTick();
            scrollToBottom();
          } else {
            // No results found
            chatMessages.value.push({
              role: "system",
              content: `🔍 No results found`,
              isDebug: true,
            });
            await nextTick();
            scrollToBottom();
          }
        }
      } catch (error) {
        chatMessages.value.push({
          role: "system",
          content: `⚠️ Search error: ${error.message || error}`,
          isDebug: true,
        });
        await nextTick();
        scrollToBottom();
      }
    }
  } catch (error) {
    chatMessages.value.push({
      role: "assistant",
      content: "Error: " + (error.message || error),
    });
    await saveChatMessage({
      role: "assistant",
      content: "Error: " + (error.message || error),
    });
  } finally {
    chatLoading.value = false;
    await nextTick();
    focusInput();
  }
}

async function saveChatMessage(msg) {
  if (!props.backendUrl || !conversationId.value) return;
  if (!["user", "assistant", "system"].includes(msg.role)) return;
  const payload = {
    conversation_id: conversationId.value,
    timestamp: Date.now(),
    role: msg.role,
    content: msg.content,
  };
  if (msg.picture_id) {
    payload.picture_id = msg.picture_id;
  }
  await fetch(`${props.backendUrl}/conversations/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function clearChatHistory() {
  if (!props.backendUrl || !conversationId.value) return;
  await fetch(
    `${props.backendUrl}/conversations/${conversationId.value}/messages`,
    { method: "DELETE" }
  );
  chatMessages.value = [];
}

// Expose focusInput for parent access
defineExpose({ focusInput });
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
            <template v-else-if="msg.role === 'system' && msg.isDebug">
              <!-- Debug info commented out for now -->
              <!--
              <div class="chat-debug-message">
                <span class="chat-username">Debug</span>
                <div class="debug-content">
                  <div
                    v-if="msg.searchResults && msg.searchResults.length > 0"
                    class="debug-thumbnails"
                  >
                    <div
                      v-for="(result, idx) in msg.searchResults"
                      :key="`${i}-${result.id}`"
                      class="debug-thumbnail"
                    >
                      <img
                        :src="`${backendUrl}/pictures/${result.id}?thumbnail=true`"
                        :alt="`Result ${idx + 1}`"
                      />
                      <span class="thumbnail-score"
                        >{{ (result.score * 100).toFixed(0) }}%</span
                      >
                    </div>
                  </div>
                  <span
                    class="chat-text"
                    v-html="renderMarkdown(msg.content)"
                  ></span>
                </div>
              </div>
              -->
            </template>
            <template v-else>
              <div class="chat-assistant-full">
                <div v-if="msg.pictureUrl" class="chat-picture-result">
                  <img
                    :src="msg.pictureUrl"
                    alt="result"
                    @load="handleImageLoad"
                  />
                </div>
                <span class="chat-username">AI</span>
                <span
                  class="chat-text"
                  v-html="renderMarkdown(msg.content)"
                ></span>
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
          <button
            class="chat-clear-btn"
            @click="clearChatHistory"
            title="Clear chat history"
            style="margin-left: 1em; font-size: 0.9em"
          >
            🗑️
          </button>
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
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 1.2em 1.5em 1em 1.5em;
  background: #f7f7fa;
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

.chat-debug-message {
  width: 100%;
  background: #f0f0f0;
  border-left: 3px solid #999;
  color: #666;
  border-radius: 4px;
  padding: 0.4em 0.8em;
  font-size: 0.85em;
  font-family: monospace;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.5em;
  margin: 0.3em 0;
  opacity: 0.8;
}

.chat-debug-message .chat-username {
  display: none; /* Hide "Debug" label to save space */
}

.chat-debug-message .debug-content {
  display: flex;
  flex-direction: column;
  gap: 0.5em;
  width: 100%;
}

.debug-thumbnails {
  display: flex;
  gap: 0.5em;
  margin-bottom: 0.5em;
}

.debug-thumbnail {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: 4px;
  overflow: hidden;
  border: 2px solid #ddd;
}

.debug-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumbnail-score {
  position: absolute;
  bottom: 2px;
  right: 2px;
  background: rgba(0, 0, 0, 0.7);
  color: white;
  padding: 2px 4px;
  font-size: 0.75em;
  border-radius: 2px;
  font-weight: bold;
}

.chat-debug-message .chat-text {
  white-space: pre-line;
  word-break: break-word;
  font-size: 0.9em;
  line-height: 1.4;
  max-width: 100%;
}

.chat-input-row {
  flex-shrink: 0;
  background: #f8fafd;
  border-top: 1px solid #e0e0e0;
  padding: 0.8em 1em;
  display: flex;
  align-items: flex-end;
  gap: 0.5em;
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
