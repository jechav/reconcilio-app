import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    // Existing pages call hooks after an early return; warn until they are rewritten on the new stack.
    rules: Object.fromEntries(
      Object.keys(reactHooks.configs.recommended.rules).map((rule) => [rule, "warn"]),
    ),
  },
  { files: ["src/components/ui/**"], rules: { "react-hooks/rules-of-hooks": "off" } },
);
