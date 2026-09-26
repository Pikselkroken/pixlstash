<script setup>
/**
 * Mints a token for `pixlstash-mcp` and hands back the client configuration
 * with the token already in it. Read-only by default; "Read and write
 * workflows" mints a full-access token and adds `--allow-write`, because every
 * workflow route is owner-only and a scoped token cannot reach them.
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
import OptionRows from "../widgets/OptionRows.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "created"]);

// Whether this page is being served from the machine PixlStash runs on.
//
// It decides whether an address is emitted at all, and the two cases want
// opposite things. On loopback, naming one is harmful: the desktop shell
// serves its window from an ephemeral port that changes every launch, so a URL
// taken from `window.location` is written into the client's config file and is
// dead by the next start-up. `pixlstash-mcp` reads the port, scheme and
// certificate from server-config.json instead, which stays right.
//
// Reached over the network, the opposite holds: there is no local
// server-config.json to read, so the default would resolve to the *agent's*
// own 127.0.0.1 and quietly find nothing. There the address is the only
// correct answer, and this page's own origin is it.
const LOOPBACK = ["localhost", "127.0.0.1", "[::1]", "::1"];
const isLoopback = computed(() =>
  LOOPBACK.includes(window.location.hostname.toLowerCase()),
);
const remoteUrl = computed(() =>
  isLoopback.value ? "" : window.location.origin,
);

const loading = ref(false);
const error = ref("");
const token = ref("");
const copied = ref("");
const mode = ref("read");
const MODES = [
  { id: "read", label: "Read-only" },
  { id: "write", label: "Read and write workflows" },
];
const allowWrite = computed(() => mode.value === "write");

// `-s user` is load-bearing. `claude mcp add` defaults to `-s local`, which
// registers the server only inside the directory it was run from, so the agent
// has no PixlStash tools anywhere else and answers questions about the library
// from the filesystem instead. A picture library is not a per-project thing.
const claudeCommand = computed(() => {
  const url = remoteUrl.value ? ` --url ${remoteUrl.value}` : "";
  const write = allowWrite.value ? " --allow-write" : "";
  return `claude mcp add -s user pixlstash -e PIXLSTASH_TOKEN=${token.value} -- pixlstash-mcp${url}${write}`;
});

const args = computed(() => [
  ...(remoteUrl.value ? ["--url", remoteUrl.value] : []),
  ...(allowWrite.value ? ["--allow-write"] : []),
]);

const configJson = computed(() =>
  JSON.stringify(
    {
      mcpServers: {
        pixlstash: {
          command: "pixlstash-mcp",
          ...(args.value.length ? { args: args.value } : {}),
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
    mode.value = "read";
  },
);

async function create() {
  error.value = "";
  loading.value = true;
  try {
    // Distinct descriptions so the token list says which row to revoke.
    const created = await createToken(
      allowWrite.value
        ? { description: "AI agent (MCP, read/write)", scope: "ALL" }
        : { description: "AI agent (MCP)", scope: "READ" },
    );
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
      <OptionRows v-model="mode" :options="MODES" aria-label="Access" />
      <template v-if="!allowWrite">
        <p class="cad-hint">
          Creates a read-only token and the configuration to paste into an MCP
          client. The agent can search this library and read pictures, tags and
          ComfyUI recipes. It has no tools that change or delete anything.
        </p>
        <p class="cad-hint">
          The token covers the whole library. Use <strong>New token</strong> if
          the agent should see only one set, character or project.
        </p>
      </template>
      <template v-else>
        <p class="cad-hint">
          The agent can also edit and store workflow graphs, and start runs
          that use your GPU and add pictures to the library. With a ComfyUI MCP
          server connected too, it can check its edits against your ComfyUI
          before storing them.
        </p>
        <p class="cad-warn">
          This needs a full-access token. Any agent that can read its own
          configuration file then has full owner control of PixlStash, whatever
          tools it is offered. Revoke it from the token list when you are done.
        </p>
      </template>
      <p v-if="error" class="cad-error">{{ error }}</p>
    </template>

    <!-- Step 2: the token is in both blocks, and is not readable again. -->
    <template v-else>
      <p class="cad-hint">
        The token is shown once and cannot be read back. Copy one of these now.
      </p>
      <p v-if="remoteUrl" class="cad-warn">
        You are viewing PixlStash over the network, so these name
        <code>{{ remoteUrl }}</code> explicitly. The agent has to be able to
        reach that address, and if PixlStash is serving https with its own
        certificate, to trust it. Running the agent on the PixlStash machine
        instead needs no address at all.
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
/* The remote case, which the copied blocks cannot solve on their own. */
.cad-warn {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  opacity: 0.9;
}
.cad-warn code {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
.cad-error {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-error));
}
</style>
