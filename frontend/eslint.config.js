import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";
import vueParser from "vue-eslint-parser";

export default [
  {
    ignores: ["dist/**", "node_modules/**"],
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
        },
      ],
      "no-useless-assignment": "warn",
      "vue/no-unused-vars": "warn",
      "vue/no-use-v-if-with-v-for": "warn",
      "vue/multi-word-component-names": "off",
    },
  },
];
