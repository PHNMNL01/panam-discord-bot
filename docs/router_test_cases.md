# Router Test Cases

Living spec pro natural file router a AI intent classifier. Slouzi k ladeni priorit mezi beznou konverzaci, aktualni prilohou, poslednim file contextem a vystupnimi soubory.

| Input text | Context | Expected target | Expected mode | Expected output_format | Poznamka |
|---|---|---|---|---|---|
| `Panam udelej z toho tabulku` | Po praci se souborem, existuje `last_file_context` | `last_file_context` | `structured_data` | `xlsx` | Obecna tabulka nad souborem ma defaultovat na XLSX. |
| `Panam dej mi to do Excelu` | Po praci se souborem, existuje `last_file_context` | `last_file_context` | `structured_data` | `xlsx` | Tvrde pravidlo, classifier neni potreba. |
| `Panam udelej z toho soubor csv` | Po praci se souborem, existuje `last_file_context` | `last_file_context` | `structured_data` | `csv` | CSV ma vyhrat nad obecnym "soubor". |
| `Panam jen testuju, ze jsi nehledala soubor` | Libovolny kanal, muze existovat predchozi soubor | `conversation` | `chat_answer` | `null` | Meta rozhovor o chovani/routeru, ne file request. |
| `Panam co je na tom spatne?` | Po bezne odpovedi Panam, bez aktualni prilohy | `conversation` | `chat_answer` | `null` | Navazuje na konverzaci, nema automaticky hledat posledni prilohu. |
| `Panam co je spatne v tom souboru?` | Aktualni priloha nebo predchozi podporovana priloha | `current_attachment` nebo `last_file_context` | `chat_answer` | `null` | Explicitni file subject + akce nad souborem. |
| `Panam shrn ten soubor` | Predchozi podporovana priloha | `last_file_context` | `chat_answer` | `null` | Explicitni akce + file subject. |
| `Panam dej mi to do souboru` | Predchozi podporovana priloha | `last_file_context` | `human_document` | `md` | Vystupni file request, default MD. |
| `Panam co je na tom spatne?` | Aktualni zprava obsahuje podporovanou prilohu | `current_attachment` | `chat_answer` | `null` | Aktualni priloha muze vyhrat u nejasneho dotazu. |
| `Panam uprav ten Excel` | Aktualni nebo predchozi Excel | `current_attachment` nebo `last_file_context` | `unsupported_direct_edit` | `null` | Puvodni soubor se primo neupravuje. |
