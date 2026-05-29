# Panam Prompts

Tahle slozka slouzi jako verzovana zaloha promptu z OpenAI Prompt Managementu.

Zdroj pravdy pro beznou osobnost Panam je konfigurace v lokalnim `.env`:

```env
OPENAI_PROMPT_ID=
OPENAI_PROMPT_VERSION=
```

Prompt texty tady jsou dokumentacni zaloha pro prehled, diffy a historii zmen. Runtime bot pouziva prompt podle `OPENAI_PROMPT_ID` a `OPENAI_PROMPT_VERSION`.

Neukladej sem:

- tokeny
- API klice
- hesla
- citliva osobni data
- realna zakaznicka, firemni nebo HR data
