import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";
import vueParser from "vue-eslint-parser";

export default [
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
    ],
  },
  js.configs.recommended,
  ...pluginVue.configs["flat/essential"],
  {
    files: ["**/*.{js,vue}"],
    languageOptions: {
      parser: vueParser,
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
        __APP_VERSION__: "readonly",
      },
    },
    rules: {
      "no-empty": "warn",
      "no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          ignoreRestSiblings: true,
        },
      ],
      "no-useless-assignment": "warn",
      "vue/no-unused-vars": "warn",
      "vue/no-use-v-if-with-v-for": "warn",
      "vue/multi-word-component-names": "off",
    },
  },
  {
    // Backend URL strings live only in src/api/ (frontend_architecture.md §8).
    // Importing the raw `apiClient` verb-caller anywhere else is how inline
    // URLs creep back in, so it is an ERROR outside that directory. The rest of
    // the module stays importable everywhere: `login`, `logout`, `checkSession`,
    // `activateShareToken`, `appendShareToken`, `setRequestClientId`,
    // `isAuthenticated`, `isReadOnly`, `sessionContext`, `API_BASE_URL` are
    // session/auth state, not transport.
    files: ["**/*.{js,vue}"],
    ignores: [
      // The resource layer itself, which is where these imports belong.
      "src/api/**",
      // The singleton's own definition: it is the one place Axios is created.
      "src/utils/apiClient.js",
      // Component/store tests mock the transport, so they import `apiClient`
      // to configure the mock rather than to call a URL. Production code is
      // what this rule is protecting.
      "**/*.test.js",
    ],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/utils/apiClient",
              importNames: ["apiClient"],
              message:
                "Add the call to a src/api/ resource module and import that instead (frontend_architecture.md §8).",
            },
            {
              name: "../utils/apiClient",
              importNames: ["apiClient"],
              message:
                "Add the call to a src/api/ resource module and import that instead (frontend_architecture.md §8).",
            },
            {
              name: "../../utils/apiClient",
              importNames: ["apiClient"],
              message:
                "Add the call to a src/api/ resource module and import that instead (frontend_architecture.md §8).",
            },
            {
              name: "./utils/apiClient",
              importNames: ["apiClient"],
              message:
                "Add the call to a src/api/ resource module and import that instead (frontend_architecture.md §8).",
            },
            {
              // A module here would re-create Axios and silently lose the
              // /api/v1 prefix, share-token injection, X-Client-Id and the
              // global 401 -> logout.
              name: "axios",
              message:
                "Use the shared apiClient singleton from src/api/ instead of a new Axios instance.",
            },
          ],
        },
      ],
    },
  },
];
