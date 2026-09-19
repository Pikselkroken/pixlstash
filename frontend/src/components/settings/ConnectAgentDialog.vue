<script setup>
/**
 * Mints a read-only token for `pixlstash-mcp` and hands back the client
 * configuration with the token already in it.
 *
 * The whole point is that nobody copies a bare secret out of the token table
 * and then hand-edits a JSON file around it: the two blocks here paste
 * straight into a client. Scope is a plain unpinned READ token - narrowing it
 * to one set, character or project is what the "New token" dialog is for, and
 * duplicating that picker here would be a second place to keep correct.
 */
import { computed, ref, watch } from "vue";
import { createToken } from "../../api/users";
import { copyText } from "../../utils/clipboard";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "created"]);

// No `--url`, deliberately. The desktop shell serves this window from an
// ephemeral loopback port that changes on every launch, so a URL derived from
// `window.location` is baked into the client's config file and is wrong the
// next time PixlStash starts. `pixlstash-mcp` reads the configured port out of
// server-config.json itself, which is the stable answer and stays right when
// the owner changes the port. `--url` remains for pointing it somewhere else.
const loading = ref(false);
const error = ref("");
const token = ref("");
const copied = ref("");

// `-s user` is load-bearing. `claude mcp add` defaults to `-s local`, which
// registers the server only inside the directory it was run from, so the agent
// has no PixlStash tools anywhere else and answers questions about the library
// from the filesystem instead. A picture library is not a per-project thing.
const claudeCommand = computed(
  () =>
    `claude mcp add -s user pixlstash -e PIXLSTASH_TOKEN=${token.value} -- pixlstash-mcp`,
);

const configJson = computed(() =>
  JSON.stringify(
    {
      mcpServers: {
        pixlstash: {
          command: "pixlstash-mcp",
          env: { PIXLSTASH_TOKEN: token.value },
        },
      },
    },
    null,
    2,
  ),
);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    loading.value = false;
    error.value = "";
    token.value = "";
    copied.value = "";
  },
);

async function create() {
  error.value = "";
  loading.value = true;
  try {
    const created = await createToken({
      description: "AI agent (MCP)",
      scope: "READ",
    });
    if (!created?.token) throw new Error("No token returned");
    token.value = created.token;
    emit("created");
  } catch {
    error.value = "Failed to create the token. Please try again.";
  } finally {
    loading.value = false;
  }
}

async function copy(key, text) {
  if (!(await copyText(text))) return;
  copied.value = key;
  setTimeout(() => {
    if (copied.value === key) copied.value = "";
  }, 2000);
}
</script>

<template>
  <AppDialog
    :open="open"
    title="Connect an AI agent"
    @close="emit('close')"
    @accept="!token && !loading && create()"
  >
    <!-- Step 1: explain what the agent gets, then mint on an explicit press. -->
    <template v-if="!token">
      <p class="cad-hint">
        Creates a read-only token and the configuration to paste into an MCP
        client. The agent can search this library and read pictures, tags and
        ComfyUI recipes. It has no tools that change or delete anything.
      </p>
      <p class="cad-hint">
        The token covers the whole library. Use <strong>New token</strong> if
        the agent should see only one set, character or project.
      </p>
      <p v-if="error" class="cad-error">{{ error }}</p>
    </template>

    <!-- Step 2: the token is in both blocks, and is not readable again. -->
    <template v-else>
      <p class="cad-hint">
        The token is shown once and cannot be read back. Copy one of these now.
      </p>

      <div class="cad-block">
        <span class="section-label">Claude Code</span>
        <p class="cad-step">Run this once in a terminal. Any folder will do.</p>
        <div class="cad-row">
          <code class="cad-code">{{ claudeCommand }}</code>
          <AppButton
            variant="ghost"
            icon-only
            :icon-left="copied === 'cli' ? 'check' : 'content-copy'"
            :tooltip="copied === 'cli' ? 'Copied!' : 'Copy command'"
            aria-label="Copy command"
            @click="copy('cli', claudeCommand)"
          />
        </div>
      </div>

      <div class="cad-block">
        <span class="section-label">Any other MCP client</span>
        <p class="cad-step">
          Goes in your client's MCP configuration file. In Claude Desktop that is
          <strong>Settings → Developer → Edit Config</strong>. If that file
          already lists servers, add the <code>"pixlstash"</code> entry inside
          its existing <code>"mcpServers"</code> block rather than replacing
          the file, or you will drop the servers already there.
          <a
            class="cad-link"
            href="https://modelcontextprotocol.io/quickstart/user"
            target="_blank"
            rel="noopener noreferrer"
          >
            Where to find it
            <v-icon size="x-small" aria-hidden="true">mdi-open-in-new</v-icon>
          </a>
        </p>
        <div class="cad-row">
          <code class="cad-code cad-code--json">{{ configJson }}</code>
          <AppButton
            variant="ghost"
            icon-only
            :icon-left="copied === 'json' ? 'check' : 'content-copy'"
            :tooltip="copied === 'json' ? 'Copied!' : 'Copy configuration'"
            aria-label="Copy configuration"
            @click="copy('json', configJson)"
          />
        </div>
      </div>
    </template>

    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">
        {{ token ? "Close" : "Cancel" }}
      </AppButton>
      <AppButton
        v-if="!token"
        variant="primary"
        :loading="loading"
        @click="create"
      >
        Create token
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.cad-hint {
  margin: 0;
  font-size: var(--text-base);
  opacity: 0.75;
}
.cad-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
/* What to actually do with the block below it. Without this the JSON is a
   puzzle: it is obvious how to copy and not at all obvious where it goes. */
.cad-step {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  opacity: 0.75;
}
.cad-step code {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
.cad-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-medium);
  text-decoration: underline;
}
.cad-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.cad-code {
  flex: 1;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  word-break: break-all;
  opacity: 0.9;
}
/* The JSON block is the one place a newline is load-bearing. */
.cad-code--json {
  white-space: pre;
  overflow-x: auto;
  word-break: normal;
}
/* `--v-theme-error`, not the `--v-theme-surface-error` ShareDialog reaches
   for: only `dark-surface-error` is registered in main.js, so that one is an
   undefined var() and renders as no colour at all. */
.cad-error {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-error));
}
</style>
