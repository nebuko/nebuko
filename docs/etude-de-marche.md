# Étude de marché — *git-flow-ai*

| | |
|---|---|
| **Date** | 2026-08-28 (édition du 2026-08-28 : extension à la section 4.4 — outils de workflow agentique) |
| **Périmètre** | Produit *git-flow-ai* (dépot [olivier-norture/git-flow-ai](https://github.com/olivier-norture/git-flow-ai)) : positionnement, marché, concurrence (revue de code + outils de workflow agentique), modèle économique, maturité |
| **Méthode de collecte** | Analyse du dépôt (README, `landscape.md`, `agent-config.yaml`, métadonnées API GitHub), de l'issue interne #475 et de la PR #481 ; recherche web (SearXNG) du 2026-08-28 sur la concurrence, le pricing et les données de marché. Pour la section 4.4 : consolidation des analyses internes existantes (issues #227, #291, #318, #319, PR #485) complétée par le recoupement direct avec les sources officielles des éditeurs (vérifiées le 2026-08-28) et les métadonnées API GitHub des dépôts concernés |
| **Lisibilité** | Document unique, ~30 min de lecture. Tout fait chiffré porte une référence `[n]` résolue dans l'index des sources (section 10). Trois niveaux d'épistémicité sont explicitement distingués : **fait vérifié** (source primaire), **signal secondaire** (blog/press citant une source primaire — marqué « à recouper »), **hypothèse**. |

---

## 1. Synthèse exécutive

**Ce qu'est *git-flow-ai*.** *git-flow-ai* est un outil d'automatisation de flux Git/GitHub propulsé par une architecture multi-agents (cinq agents IA aux rôles distincts : architecte, collecte de contexte, développement, revue, raffinement de ticket), exécutée nativement dans GitHub Actions et coordonnée par la filature de commentaires des issues elles-mêmes [52][50]. Sa proposition de valeur tient en trois lignes :

1. La **délégation entre agents spécialisés** (`@@mention`) — compétence clé de toute équipe d'ingénierie, absente des assistants IA actuels, qui sont des agents uniques [50].
2. Le **processus d'équipe comme artefact versionné** : des workflows déclaratifs en YAML, commités, diffables, roll-backables, partables — avec des *gates* d'approbation humaine conservés sur les décisions de conception [50].
3. Une **distribution zero-copy** : n'importe quel dépôt GitHub adopte le flux par une référence de *reusable workflow*, sans copier de code local [52].

**Pourquoi le moment est favorable.** Deux convergences rendent ce créneau chaud en 2026 :

- **La crise de la revue de code** : la génération de code par IA a déplacé le goulot d'étranglement du pipeline vers la revue humaine. Les données disponibles indiquent un temps de revue médian en forte hausse (+441,5 % selon Faros AI 2026, à recouper [34]) et des PRs assistées IA d'environ 2,5× plus grosses avec des attentes de revue ~5× plus longues (LinearB, à recouper [34]). Une étude randomisée de METR montre même que des développeurs expérimentés sont 19 % **plus lents** avec l'IA quand ils *pensent* être ~20 % plus rapides [30][31] — un écart perception/réalité qui renforce la demande pour des garde-fous de processus [32].
- **La concentration du capital sur un sous-segment** : la review assistée par IA attire des valorisations massives (CodeRabbit : $1,5 Md [1] ; Cognition/Devin : négociations à ~$25 Md [11]) mais, à la date de cette étude, **aucun acteur identifié ne possède le segment « processus d'équipe orchestré par des agents multiples »** — l'espace revendiqué par *git-flow-ai* [1][4][8][11].

**État réel du projet, assumé.** Le dépôt a été créé le 2026-05-29 ; au 2026-08-28 il comptait 0 star, 0 fork, 0 watcher et 79 issues ouvertes [54]. Le produit est en pré-alpha, au Gate 0 d'un plan de lancement en quatre portes [48][54]. Cette étude assume ces chiffres plutôt que de les masquer : ils sont publics et vérifiables sur GitHub, et leur transparence est considérée ici comme un signal de maturité plutôt qu'un aveu.

**En une phrase.** *git-flow-ai* positionne un format ouvert (workflow YAML multi-agents) sur le segment non occupé de l'orchestration du travail d'équipe par agents IA, porté par une base GitHub de 150 M+ de personnes et 420 M+ de dépôts [47], avec le risque symétrique d'une fenêtre de tir limitée face à des entrants très capitalisés [1][11].

---

## 2. Le produit

### 2.1 Architecture : cinq agents aux rôles distincts

Le système s'appuie sur l'architecture multi-agents d'OpenHands [52][44] :

| Agent | Rôle | Contrainte clé |
|---|---|---|
| `@@genius` (Principal Engineer) | Architecture, analyse logique, plans techniques | Délègue la revue à `@@review` |
| `@@ctx` (Context Agent) | Collecte de contexte profond (code, issues, PRs, docs) avec localisation et extraits | **Lecture seule** — ne modifie jamais le code |
| `@@dev` (Execution Developer) | Implémentation, tests, validation | Exécute les plans |
| `@@review` (Senior Code Reviewer) | Inspection des diffs, bugs, régressions, verdict | Terminal pour `git diff`, **pas de modification** |
| `@@issue` (Issue Refinement Agent) | Aide les humains à produire des issues claires et actionnables | Itératif avec l'humain [52] |

Chaque agent est défini déclarativement (rôle, personnalité, modèle, skills) et connaît le reste de la *roster*. Le mécanisme central est la **délégation par `@@mention`** : n'importe quel agent peut, à n'importe quelle étape, déléguer (« j'ai besoin du contexte sur X », « `@@dev`, implémente ce stub », « `@@review`, vérifie ce diff ») ; le résultat rebondit dans la même filature d'issue, où l'agent appelant reprend [50].

### 2.2 Distribution zero-copy

L'adoption par un dépôt externe se résume à : une référence de *reusable workflow* (`uses: olivier-norture/git-flow-ai/.github/workflows/openhands-multi-agent.yml@main`), un fichier `agent-config.yaml`, et deux secrets (`OPENROUTER_API_KEY`, `GH_PAT`) sur un runner self-hosted [52]. Les workflows, prompts et scripts sont récupérés depuis le dépôt *git-flow-ai* à chaque exécution : **aucune copie de code côté utilisateur**, et les mises à jour sont donc transparentes. Le point de friction restant est le runner self-hosted, exigence actuelle du produit [52].

### 2.3 Configuration YAML en trois couches

Au lieu de dizaines de paramètres plats dupliqués, la configuration est stratifiée [52][53] :

| Couche | Contenu | Exemple |
|---|---|---|
| **Providers** | Endpoint API + variable d'authentification | `openrouter`, `local_vllm` |
| **LLMs** | Modèle, provider, température, paramètres de sampling | `deepseek-v4-pro`, `qwen-3-6-30b` |
| **Agents** | Rôle → modèle + prompt + skills + overrides par agent | `genius`, `dev`, `ctx`, `review`, `issue` |

Cette séparation est aussi ce qui rend le routage de modèles **pluggable** (OpenRouter, vLLM local, autre provider) — un élément structurant du modèle économique (section 6) [50][53].

### 2.4 Workflows déclaratifs et gates humains

Un workflow est un document YAML versionné qui encode comment le travail se fait réellement : étapes ordonnées, chacune liée à un rôle d'agent, avec passage de contexte et **gates d'approbation humaine** aux points où l'humain doit valider avant que la machine continue [50]. Le workflow **flagship** est *spec-driven* (spec → gate humain → stubs → gate humain → implement → verify), construit en dogfooding à partir d'un usage réel plutôt que d'usages imaginés [50][48].

La thèse centrale : l'attention humaine se concentre là où elle rapporte — la spec d'architecture, les stubs (points d'injection, call sites, cas de test) et le verdict — plutôt que sur chaque ligne d'une grosse PR générée par IA. La charge de revue passe de « chaque ligne » à « deux gates de niveau conception » [50].

### 2.5 Accroche stratégique

L'ambition est exprimée en trois paliers : **« Capability, then product, then platform »** — d'abord la capacité (agents qui se délèguent), puis le produit (workflows qui encodent le travail d'une équipe), puis la plateforme (bibliothèque de processus partagés et réutilisables) [50][51].

---

## 3. Le marché et ses tendances

### 3.1 Le marché des agents IA

Le marché est celui de l'« agentic AI » : systèmes d'IA capables de planifier et d'exécuter des tâches de bout en bout avec une certaine autonomie [42][43]. Plusieurs rapports de marché couvrent sa trajectoire sur la période 2026–2030 (The Business Research Company [39], MarketsandMarkets [40], Analytics Insight [41]) ; **les valeurs chiffrées TAM/SAM n'ont pas été extraites de ces rapports lors de la collecte** et figurent dans la liste des actions d'approfondissement (section 9, point e). Ce qui est établi par contre : la dynamique du secteur est portée par des financements très lourds concentrés sur la couche « agent IA pour développeur » (section 4), et par la pénétration massive d'outils IA dans les flux d'ingénierie — ce qui a précisément créé le pain point de la revue [30][34].

### 3.2 La base adressable : GitHub

GitHub revendique **150+ millions de personnes** et **420+ millions de dépôts** [47]. C'est la base du calcul de marché adressable pour *git-flow-ai* : l'outil ne demande aucune infrastructure nouvelle — *GitHub Issues and Comments sont la couche de transport* [50]. Toute équipe qui ouvre une issue sur GitHub est dans le périmètre potentiel ; les cibles prioritaires (équipes de 5 à 50 développeurs utilisant déjà des outils IA de génération) constituent le segment prioritaire identifié dans le plan de lancement [48].

### 3.3 La crise de la revue de code : le pain point n°1

C'est le cœur du positionnement, et il est documenté de plusieurs façons :

- **METR (étude randomisée contrôlée, juillet 2025)** : 16 développeurs expérimentés travaillant sur des dépôts réels à maintenance longue durée, 246 tâches — **avec accès aux outils IA, ils ont mis 19 % de temps en plus** ; or, après coup, ils estimaient en moyenne que l'IA les avait rendus ~20 % plus rapides [30][31][32]. Leçon : la productivité perçue n'est pas la productivité mesurée, et le déséquilibre entre volume généré et capacité d'assimilation humaine est structurel [33].
- **Goulot de revue (signaux secondaires — à recouper avec les rapports primaires)** : Faros AI 2026 (22 000 développeurs) : temps de revue médian **+441,5 %** pendant que le throughput augmente de **+33,7 %** [34] ; chiffres alternatifs circulant dans des blogs : « 98 % de PRs mergées en plus, +91 % de temps de revue » [35][36][37]. LinearB (8,1 M de PRs) : les PRs assistées par IA sont **~2,5× plus grosses** et leur attente de revue est **~5× plus longue** [34] ; un blog secondaire rapporte que **61 % des PRs générées par IA ne voient jamais d'œil humain** [38].
- **Conséquence pour le marché** : le facteur limitant du pipeline logiciel n'est plus la génération mais l'assimilation humaine du code [50]. Tout ce qui réduit le ratio « lignes générées / lignes jugées par un humain » a une valeur directe pour l'équipe — c'est exactement ce que revendiquent la revue assistée (CodeRabbit, Copilot, Diamond…) et, à un autre niveau, *git-flow-ai* (réduire ce qu'il y a à juger en gardant des gates de conception) [50].

### 3.4 Autres tendances structurelles

- **Le code est produit par des machines, jugé par des humains** : chaque acteur concurrent pivote vers des capacités « agentic » de gestion du changement [2][4][8] — le marché valide la direction, pas encore le degré d'autonomie.
- **La délégation est la compétence manquante** : les assistants actuels sont des agents uniques, sans rôles, sans roster, sans mécanisme pour confier le travail à un spécialiste [50]. La délégation inter-agents est peu ou pas offerte commercialement à la date de cette étude (absence de concurrent direct identifié, section 4.3).
- **Le processus d'équipe comme connaissance tribale** : non versionné, non partageable, perdu quand les gens partent [50]. Versionner le processus (workflows-as-code) est une réponse simple et sans infrastructure.
- **Les coûts d'inférence divergent de 20 à 100×** selon les modèles (section 6) — le routage économique des tâches entre modèles devient un levier de marge concret [53].

---

## 4. Paysage concurrentiel

### 4.1 Tableau comparatif (données vérifiées le 2026-08-28)

| Acteur | Segment | Financement / valorisation | Pricing (ordre de grandeur) | Signal clé |
|---|---|---|---|---|
| **CodeRabbit** | Revue de code IA | Série C **$143 M @ $1,5 Md** (2026-08-12) [1] ; Série B $60 M < 1 an avant [1] | Free → Pro ~$19–30/utilisateur/mois → Enterprise [14][15][16] | Pivot annoncé « Agentic Change Management » [2][3] |
| **GitHub Copilot Code Review** | Revue IA intégrée native à GitHub | Non applicable (Microsoft) | Consommée en *premium requests* [18] ; depuis 2025-12-17 accessible aux membres d'org **sans licence Copilot**, facturée en premium requests [19] | Distribution via la plateforme dominante [17][20][21] |
| **Graphite** | Stacked PRs + revue | Série B **$52 M** ; lancement de l'agent de revue IA **Diamond** [4][5][6] | Non documenté lors de la collecte | Positionnement « Code review for the age of AI » [7] |
| **Greptile** | Revue de code IA | Série A **$30 M @ $180 M**, lead Benchmark (Eric Vishria) ; alumni YC, fondée 2023 par Dasksh Gupta [8][9][10] | Non documenté lors de la collecte | Présence dans les comparatifs 2026 de tête [26][27][28] |
| **Devin / Cognition** | Agent-ingénieur autonome (incl. Windsurf) | Négociations rapportées à **~$25 Md** (2026-05) [11][13] ; « ARR doublé » [12] | Free / Pro / Max / Teams / Enterprise ; modèle ACU (Agent Compute Unit) : Core $20/mois + ~$2,25/ACU ; Team ~$500/mois incluant 250 ACU @ ~$2,00/ACU (source secondaire [23][24][25]) | L'agent comme substitut, pas comme collaborateur [11][12] |
| **Qodo (QodoMerge)** | Revue/merge assistés | Non documenté lors de la collecte | Comparatifs 2026 [26][28][29] | Acteur récurrent des classements |

> **Note épistémique** : les montants de financement proviennent de sources primaires ou quasi-primaires (Reuters [1], BusinessWire [2], blog officiel Graphite [4]) sauf mention contraire. Les prix exacts des pages officielles n'ont pas été scrapés ligne à ligne ; seuls les ordres de grandeur issus des snippets collectés sont garantis [15][23].

### 4.2 Lecture stratégique

Trois constats ressortent du tableau :

1. **Le capital concentre la *review assistée*.** C'est le segment le plus financé ($1,5 Md pour CodeRabbit [1], ~$25 Md pour Cognition [11]) et le mieux défendu (l'intégration native de Copilot dans GitHub [17][19]). Compétiter frontalement sur « un autre outil de review IA » serait une bataille perdue d'avance face à des acteurs subventionnés.
2. **Personne ne possède le *processus d'équipe orchestré*.** Tous les acteurs ci-dessus automatisent **une étape** (la revue, le merge, le diffing). Aucun ne propose des agents **multiples à rôles** qui se **délèguent** et encodent **le processus complet** (spec → design → implémentation → vérification) avec gates humains — c'est l'espace revendiqué par *git-flow-ai* [50].
3. **Les plateformes natives descendent vite.** Copilot Code Review est devenu disponible sans licence Copilot en décembre 2025 [19] ; le risque principal est la convergence des plateformes natives (GitHub Actions + Copilot) vers l'orchestration multi-étapes (menace, section 8).

### 4.3 Concurrents open-source : absence notable

Les recherches menées du 2026-08-28 n'ont retourné **aucun concurrent open-source direct identifiable** sur le segment « orchestration multi-agents du processus d'équipe dans GitHub Actions » [55]. Des outils OSS de voisinage existent (clients Git, automatisations CI), mais aucun n'occupe le croisement *workflows-as-code* × *délégation inter-agents* × *GitHub-native*. **Caveat** : ce constat tient à la qualité des résultats de recherche obtenus (moteur de qualité variable, requêtes françaises dominées par du bruit) et doit être confirmé par un audit ciblé des dépôts GitHub (section 9, point g). L'absence apparente est néanmoins un signal : soit le segment est vierge, soit il n'a pas encore été formulé comme tel.

### 4.4 Outils de workflow agentique : concurrence de niveau framework

Le tableau 4.1 couvre les acteurs du segment « revue de code ». *git-flow-ai* étant aussi un outil d'orchestration de workflows multi-agents, une extension de l'étude au périmètre « workflow agentique » était due : frameworks d'orchestration d'agents (LangGraph, CrewAI, AutoGen), plateforme d'orchestration déclarative (Kestra) et solution native GitHub (GitHub Agentic Workflows / gh-aw). Ce sous-domaine a déjà fait l'objet d'analyses internes approfondies (issues #227, #291, #318, #319, PR #485) ; cette section les consolide au gabarit du document principal et complète les lacunes de recherche initiale par recoupement direct avec les sources officielles des éditeurs, vérifiées le 2026-08-28. Les métadonnées des dépôts (stars, forks, licence, date de création) proviennent de l'API GitHub relevée le 2026-08-28 [56].

**Note de périmètre.** Le terme « GitHub Agent » de la demande initiale désigne, à la date de cette étude, **GitHub Agentic Workflows (gh-aw)** : le projet GitHub Next « write agentic workflows in natural-language Markdown, run them in GitHub Actions » [57][58]. S'agissant éventuellement du *Copilot coding agent*, voir la distinction en 4.4.5 : c'est un acteur différent, déjà couvert en revue de code dans le tableau 4.1. De même, « kaesra » (sic) désigne **Kestra** (cf. issue interne #319).

#### 4.4.1 Tableau comparatif (données vérifiées le 2026-08-28)

| Outil | Nature | Paradigme d'orchestration | Contrôle & déterminisme | Human-in-the-loop | Intégrations & observabilité | Licence | Adhésion (⭐ / forks) | Cas d'usage dominant |
|---|---|---|---|---|---|---|---|---|
| **LangGraph** [59] | Runtime/SDK code-first (Python/JS), partie de l'écosystème LangChain | Graphe de machine à états Pregel : `StateGraph`, nœuds/arêtes codés, sous-graphes | Fort — contrôle programmatique fin ; parallélisme, conditions, retries par branchement d'état | ⚠️ manuel (`interrupt()` + `Command(resume=…)`) ; un nœud d'approbation first-class reste une demande ouverte ([langgraph #8026](https://github.com/langchain-ai/langgraph/issues/8026)) [59] | ✅ LangSmith : tracing, evals, déploiement managé [59] | MIT [56] | 40 606 / 6 839 (créé 2023-08) [56] | Agents résilients de production, longs, avec état durable |
| **CrewAI** [60] | Bibliothèque Python multi-agents | Équipes de « rôles » : Agents (rôle/objectif/outils) + Tasks + Crew, processes séquentiels / hiérarchiques / consensuels | Moyen — abstractions de haut niveau ; le pattern manager-worker est critiqué pour sa fiabilité (signal secondaire, à recouper) [61] | Via `wait_for_input()` (entrées utilisateur explicites) [60] | Traçage de base ; observabilité poussée via outils tiers (Langfuse etc.) [61] | MIT [56] | 57 723 / 8 269 (créé 2023-10) [56] | Prototypage rapide d'agents en jeu de rôles ; applications métier multi-agents |
| **AutoGen** [62] | Framework Microsoft de conversations multi-agents | Agents conversables + group chat coordonné par un gestionnaire (sélection de l'orateur) | Faible/modéré — dynamique conversationnelle, difficile à déboguer pour des pipelines déterministes [61] | Natif — l'humain peut participer au group chat [61] | Logging de base ; héritage Semantic Kernel côté enterprise [62] | CC-BY-4.0 (répertoire) [56] | 60 668 / 9 160 (créé 2023-08) [56] | Recherche/exploration de patterns multi-agents ; **fusion en cours dans le Microsoft Agent Framework** [62] |
| **Kestra** [63] | Plateforme d'orchestration & scheduling event-driven (Java/Rust + workers + BDD) | Pipelines déclaratifs YAML : tasks, dépendances, `ForEach`/`Switch`/`Parallel`, langage d'expressions complet | Très fort — déterministe : cron, retries avec backoff, timeouts, checkpointing, idempotence [63][64] | Task `Pause` explicite (pause jusqu'à reprise manuelle/webhook), SLA configurables [64] | ✅ UI web (DAG temps réel, logs, métriques) ; 1 600+ plugins (Kafka, S3, HTTP, Slack, dbt…) [63] | Apache-2.0 [56] | 27 944 / 2 952 (créé 2019-08) [56] | Orchestration data/infrastructure : ETL, ML pipelines, automatisation infra critique |
| **GitHub Agentic Workflows (gh-aw)** [57] | Projet GitHub Next : workflows agentiques en Markdown exécutés dans GitHub Actions | Agent unique par workflow + sub-agents inline (`## agent: name`), compilés vers `.lock.yml` | Fort par conception sécurité : lecture seule par défaut, écritures via allowlist `safe-outputs`, contrainte single-job | Native GitHub (actions) ; gates par structure du workflow plutôt que nœud dédié | Native GitHub (CLI `gh aw` : compile/logs/audit) ; multi-moteur : Copilot, Claude, Codex, Gemini [57][58] | Non documentée lors de la collecte (projet expérimental) [57] | Non documentée (projet expérimental) [57] | Triage/classification à haute volumétrie, automatisations simples, pré-fetch de données |
| **Microsoft Agent Framework (MAF)** [65][66] | SDK code-first multi-langues (Python/.NET/Go), consolidation AutoGen + Semantic Kernel | Agents + workflows graphe en code : séquentiel, concurrent, handoff, group collaboration ; exécution durable, checkpointing, time-travel, streaming [65] | Moyen — contrôles d'exécution durables mais déterminisme conversationnel ; HITL non forcé au niveau étape | Granularité appel-outil / exécuteur de workflow : approbations d'appels de fonctions, `request_info` — **pas** de gate de niveau étape forcé par un moteur [65] | ✅ OpenTelemetry natif ; matrice d'hébergement cloud/Azure (Functions, conteneurs, Durable Task, Foundry-hosted, A2A, MCP) ; aucun transport GitHub-issues, l'unique point de contact GitHub est Copilot en tant que provider LLM [65] | MIT [56] | 13 474 / 2 300 (créé 2025-04) [56] | Agents/workflows « production-grade » embarqués dans une application Python/.NET/Go, hébergés sur l'infrastructure Azure/entreprise |

> **Note épistémique** : les lignes LangGraph, CrewAI, Kestra reposent sur sources primaires (sources officielles des éditeurs [59][60][63]) recoupées avec les métadonnées API GitHub [56] et les analyses internes #227/#319. La ligne **Kestra** avait échoué lors de la recherche initiale (SearXNG) : elle a été comblée par consultation directe des sources officielles — les points restant non vérifiés (pricing détaillé, statuts de versions) sont simplement marqués « non documenté ». La ligne **gh-aw** provient des documents officiels du projet (repo `github/gh-aw`, docs github.github.com, page GitHub Next) [57][58] ; son statut *expérimental* et l'absence de données d'adhésion publiées sont assumés. La fusion AutoGen → Microsoft Agent Framework est rapportée par la documentation du dépôt [62] (source primaire). La ligne **MAF** repose sur le README du dépôt, l'ADR 0037 (Agent Skills) et un relevé API GitHub effectué le 2026-09-11 [56][65][66].
>
> **AutoGen en ligne secondaire** : conformément au compromis de profondeur uniforme, AutoGen ne reçoit pas de fiche dédiée (fiches en 4.4.2–4.4.6) — sa dynamique principale (consolidation Microsoft, incertitude de roadmap) est portée par la ligne du tableau ci-dessus [62]. La consolidation Microsoft elle-même fait l'objet de la fiche MAF en 4.4.6 [65][66].

#### 4.4.2 Fiche — LangGraph

**Ce que c'est.** Runtime code-first d'agents : machine à états de type Pregel où l'on construit une `StateGraph` en Python/JS — nœuds, arêtes et partage d'état définis dans le code, avec persistance durable via checkpointers (Postgres, etc.) [59]. La stack commerciale est LangGraph OSS + LangSmith Deployment + LangSmith observability [59]. C'est un **SDK applicatif** : il s'emballe dans une application déployée sur votre infrastructure, il n'existe pas en mode « fichier déposé dans un dépôt ».

**Points forts.** Expressivité maximale du contrôle d'agent (parallélisme DAG, sous-graphes conditionnels, retries par branchement d'état) [59] ; état durable, interrogable, replayable (time-travel) [59] ; observabilité/déploiement managé de grade production via LangSmith [59] ; agnosticisme de modèle via les intégrations LangChain [59] ; gravité d'écosystème (40 k stars, éditeur actif) [56].

**Points faibles / limites.** Courbe d'apprentissage machine à états ; sur-engineeré pour des workflows simples [61] ; le workflow n'est **pas un artefact portable** — c'est du code : aucune couche déclarative partagable en amont (aucune demande de format YAML/declaratif sur leur tracker, vérifié 2026-08-25 dans la PR #485) [59] ; human-in-the-loop manuel tant que [langgraph #8026](https://github.com/langchain-ai/langgraph/issues/8026) n'est pas livré [59] ; dépendance à l'écosystème LangChain ; s'exécute uniquement sur les environnements qu'on lui fournit — pas de transport GitHub natif, donc pas non plus de déclenchement par issue/comment ni de gate humain sous forme de commentaire [59].

**Verdict.** Complément solide, pas challenger : LangGraph gagne sur l'expressivité de la logique intra-agent et la gravité d'écosystème ; il ne peut structurellement pas offrir de workflow GitHub-native zéro-infrastructure, de roles multi-agents déclenchés par mention avec gates humains, ni l'adoption « copiez un seul YAML dans n'importe quel dépôt ». L'état cible raisonnable est hybride : coquille GitHub-native + un graphe LangGraph *à l'intérieur* d'une étape quand la logique intra-agent devient riche [59].

#### 4.4.3 Fiche — CrewAI

**Ce que c'est.** Bibliothèque Python de « crews » multi-agents : on définit des agents avec rôle/objectif/tools, des tâches assignées, et une *Crew* qui orchestre selon des processus **séquentiel, hiérarchique ou consensuel** [60]. Positionnement prototype→production d'applications agentiques métier, avec forte communauté.

**Points forts.** Abstractions intuitives de jeu de rôles, prototypage très rapide [60][61] ; flexibilité des modes de processus (séquentiel/hiérarchique/consensuel) [60] ; agnosticisme de LLM (via LiteLLM) [61] ; plus grosse communauté du périmètre (57,7 k stars) [56] ; entrée utilisateur native (`wait_for_input()`) [60].

**Points faibles / limites.** Pattern manager-worker critiqué pour sa fiabilité en production (signal secondaire, à recouper) [61] ; orchestration centrée sur la conversation/rôles, moins sur le contrôle de flux déterministe fin [61] ; observabilité limitée nativement [61] ; aucune intégration plateforme (GitHub ou autre) : le runtime et l'infrastructure restent à provisionner [60] ; état persisté principalement en mémoire [61].

**Verdict.** Concurrent au sens large sur « faire collaborer des agents », mais dans le segment « construire son application agentique » — pas dans celui du processus d'équipe versionné. Sa force (rapidité de prototypage en Python) est précisément ce qui l'éloigne de l'argument *git-flow-ai* (processus comme artefact diffable, transport GitHub, adoption zero-copy) [50]. À surveiller pour sa dynamique communautaire plus que pour une menace directe sur le quadrant revendiqué.

#### 4.4.4 Fiche — Kestra

**Ce que c'est.** Plateforme d'orchestration & scheduling event-driven open source (Apache-2.0) : flows YAML déclaratifs orientés data/infrastructure, exécutés par des workers dédiés avec une base de données (PostgreSQL/MySQL) [63][64]. Créé en 2019, bien antérieur à la vague « agents IA » — c'est un outil *d'avant* l'agentique, réutilisable pour y faire tourner des tâches appelant des agents.

**Points forts.** Déterminisme et garanties d'exécution d'entreprise : cron/scheduling, retries avec backoff, timeouts, checkpointing, idempotence [64] ; DSL riche (langage d'expressions, `ForEach`/`Switch`/`Parallel`, typage des inputs/outputs) [64] ; écosystème massif — 1 600+ plugins couvrant l'outillage data/infra (Kafka, S3, HTTP, Slack, dbt, import Airflow…) [63] ; UI web avec DAG en temps réel et gestion des exécutions [63] ; self-hosting complet (Docker/Kubernetes) [64].

**Points faibles / limites (relativement au périmètre agentique).** Zéro sémantique agentique native : les tasks exécutent des programmes déterministes, elles ne « raisonnent » pas — orchestrer des agents LLM exige de construire un plugin custom [64] ; human-in-the-loop par task `Pause` générique (pause/reprise), pas de gate d'approbation de niveau conception ancré dans un fil de discussion [64] ; lourd à opérer (service Java/Rust + workers + BDD) là où *git-flow-ai* tourne dans GitHub Actions [64] ; domaine cible différent (data engineers/DevOps, scope multi-tenant entreprise vs contexte d'une issue) [64].

**Note de limitation.** La recherche web initiale (SearXNG) sur Kestra ayant échoué (bruit moteur), cette fiche est établie sur recoupement direct des sources officielles [63][64] (vérifiées le 2026-08-28) et de l'analyse interne #319 ; les points non vérifiables (pricing, versions) ne sont pas affirmés.

**Verdict.** Ils résolvent des problèmes différents : Kestra est un orchestrateur de *tâches* pour data/infra, *git-flow-ai* un coordinateur d'*agents* encastré dans le workflow de développement. Pas de chevauchement de capacités cœur ; l'usage complémentaire naturel serait de lancer des flows Kestra (ETL, pipelines) depuis une étape d'un workflow agentique.

#### 4.4.5 Fiche — GitHub Agentic Workflows (gh-aw)

**Ce que c'est.** Projet **GitHub Next** : « write agentic workflows in natural-language Markdown, and run them in GitHub Actions » [57][58]. Un workflow est un fichier Markdown (frontmatter YAML : `strict`, `bots`, `engine`, `tools`, `safe-outputs`, `imports`, `network`) compilé vers `.lock.yml` avant exécution ; les sub-agents sont définis inline et héritent du contexte/moteur du parent [57]. Multi-moteur par design : GitHub Copilot, Claude, Codex, Gemini [57][58].

**Points forts.** Posture sécurité première : lecture seule par défaut, écritures uniquement via allowlists `safe-outputs` [57] ; validation au moment de la compilation (`gh aw compile`), outillage CLI natif (`new`/`logs`/`audit`) [57] ; neutralité de moteur (indépendance vis-à-vis d'un SDK unique) [58] ; intégration GitHub native évidente pour les équipes déjà sur Actions [57].

**Points faibles / limites.** Statut **expérimental** (GitHub Next) sans engagement de roadmap formel documenté lors de la collecte [58] ; contrainte **single-job** par conception : pas de fan-out/fan-in multi-jobs, pas de chaînage de workflows à travers des jobs, pas de gates humains entre jobs [57] ; un seul agent par workflow (les sub-agents partagent le contexte du parent : ce sont des « fonctions », pas des services isolés) [57] ; pas de grappe de rôles spécialisés avec délégation inter-agents ; la sécurité-by-default est une force en dépôt non fiable mais un verrou pour des pipelines agentiques autonomes à écritures multiples.

**Distinction demandée.** Ne pas confondre gh-aw avec les agents produit de la plateforme Copilot : le **Copilot coding agent** (agent qui implémente des issues et ouvre des PRs) et le **Copilot code review** (revue IA intégrée, couverte en 4.1 [17][18]). Ce sont des entités distinctes de gh-aw, outil de *définition* de workflows agentiques. Les trois convergent dans la même plateforme — ce qui renforce la menace de « descension de la plateforme native » (section 8), pas le segment framework.

**Note de limitation.** Les documents officiels du projet ont été consultés directement (repo `github/gh-aw`, docs `github.github.com/gh-aw`, page GitHub Next) car la recherche web initiale était bruitée [57][58] ; statut expérimental, licence et adhésion non documentées à la date de la collecte.

**Verdict.** Complémentaire sur les automatisations simples et à haute volumétrie (triage, classification), concurrent potentiel uniquement si GitHub y ajoute le chaînage multi-jobs et l'orchestration multi-agents — ce que la contrainte single-job de leur modèle sécurité active rend peu probable à court terme [57]. Le risque réel est le positionnement marketing : « agentique dans GitHub Actions » est une accroche que *git-flow-ai* doit clarifier face à gh-aw (« workflows YAML multi-agents vs Markdown single-agent »).

#### 4.4.6 Fiche — Microsoft Agent Framework (MAF)

**Ce que c'est.** SDK open source multi-langues (Python, .NET, Go — ce dernier dans un dépôt séparé `microsoft/agent-framework-go`) pour construire, orchestrer et déployer des agents et workflows multi-agents « production-grade » [65][66]. MAF est la **consolidation officielle d'AutoGen et de Semantic Kernel** : la documentation MS Learn publie des guides de migration explicites depuis chacun des deux précurseurs (dont `from-autogen`) [66] — la consolidation signalée par la ligne AutoGen du tableau 4.4.1 [62] est désormais l'état de fait. C'est un **SDK à embarquer** (`pip install agent-framework`) : les agents et workflows se définissent en code, hébergés sur Azure Functions, conteneurs, Durable Task, agents hébergés Foundry (2 lignes de code), A2A ou MCP [65]. Métadonnées vérifiées le 2026-09-11 : MIT, créé 2025-04, 13 474 ⭐ / 2 300 forks / 625 issues ouvertes, rythme de release quasi hebdomadaire côté Python (`python-1.18.0` le 2026-09-10, `dotnet-1.20.0` le 2026-08-31) [56][65].

**Abstractions clés.** Workflows en graphe avec patterns séquentiel, concurrent, handoff et group collaboration ; exécution durable (extension séparée `agent-framework-durable-extension`), checkpointing, time-travel et streaming en premier lieu ; OpenTelemetry intégré ; agents déclaratifs en YAML (`declarative-agents/`, p. ex. `DeepResearch.yaml` — pattern Magentic à trois agents Research/Planner/Manager) dont le format de workflow est généré par DevUI au format Activities (`kind:`/`id:` + actions, trigger `OnConversationStart`) — pas conçu pour l'écriture à la main [65]. **Agent Skills** (ADR 0037, statut *proposé* au 2026-03-23) : compétences multi-sources exposées au modèle par divulgation progressive via trois outils — `load_skill(name)` (corps complet), `read_skill_resource(name, resource)`, `run_skill_script(name, script, args?)` — le système ne listant que noms + descriptions dans le prompt system [65].

**Human-in-the-loop.** À la granularité **appel-outil / exécuteur de workflow** : approbations d'appels de fonctions (locale et distante/MCP, ADR 0006 *accepted*) et `request_info` ; rien ne force un arrêt à une étape prédéfinie du workflow — pas de sémantique `human_gate` équivalente au gate moteur de *git-flow-ai*, et le HITL est demandé par l'agent, pas imposé par l'orchestrateur [65].

**Points forts.** Poids Microsoft + licence MIT sans friction copyleft pour les consommateurs enterprise ; distribution et cadence de releases exceptionnelles pour un framework si jeune [56][65] ; multi-langue là où *git-flow-ai* est Python-only ; observabilité standard (OTel), durabilité, checkpointing/time-travel comme features de base [65] ; matrice provider/hébergement très large (Foundry, Azure OpenAI, OpenAI, Copilot SDK, A2A, MCP) [65].

**Points faibles / limites (relativement au périmètre *git-flow-ai*).** Embarqué dans une application : aucune surface de collaboration, aucun audit trail lisible là où les humains travaillent déjà ; aucun transport GitHub-issues (recherche de code confirmée le 2026-09-11 : le seul point de contact GitHub est **Copilot en tant que provider LLM**, pas d'orchestration déclenchée par issue ni de commentaires comme état) [65] ; HITL non forcé au niveau étape — la boundary de sécurité n'est pas un invariant moteur ; hébergement centré Azure, story plus faible pour « exécuter dans mon CI existant sur runners self-hosted » [65] ; le YAML de workflow est généré par DevUI, illisible à maintenir à la main ; Agent Skills encore au statut *proposé* ; pas d'analogue de la décomposition en sous-issues trackables (*splitPlan*).

**Verdict.** **Adjacent, pas chevauchant.** MAF répond à « comment construire et exécuter des agents dans mon logiciel » ; *git-flow-ai* répond à « comment l'IA travaille là où mon équipe travaille déjà — dans le fil d'issue ». Menace à court terme : **faible** (aucune opération au niveau collaboration issue/PR, hébergement Azure-centré). Menaces à moyen terme, concrètes : (a) **gravité enterprise** — si les entreprises standardisent sur MAF + Foundry pour l'ops agentique, elles n'iront peut-être jamais chercher un orchestrateur GHA — menace de *distribution*, pas technique ; (b) **convergence de capacités** — Skills à divulgation progressive, checkpointing/time-travel et OTel élèvent la barre du table-stakes ; (c) **affaiblissement du différenciateur YAML** — les workflows YAML déclaratifs versionnés existent désormais upstream, il faut donc posséder le différenciateur irrépliable *dans une bibliothèque* : le fil d'issue comme état, audit trail et canal humain. **Posture stratégique :** ne pas concurrencer MAF sur la construction d'agents ; double down sur ce qu'une bibliothèque ne peut structurellement pas faire (gate moteur, état/audit issue-native, distribution zero-copy, splitPlan) et rester plateforme-agnostic en couche 1 pour qu'un agent MAF/LangGraph/autre puisse être une étape d'un workflow *git-flow-ai* — on possède *où et comment le travail circule*, les frameworks possèdent *ce que sont les agents*.

**Suites engagées (comparaison #563).** Du comparatif détaillé [65][66], quatre idées étaient retenues ; les trois suivantes ont été ouvertes en issues de backlog : skills à divulgation progressive (`load_skill`, issue **[#570](https://github.com/olivier-norture/git-flow-ai/issues/570)**) ; checkpoint/resume formel du moteur (`/workflow resume --from <step>`, idempotence des effets de bord, issue **[#571](https://github.com/olivier-norture/git-flow-ai/issues/571)**) ; définitions d'agents versionnées et redistribuables (YAML comme artefact, issue **[#572](https://github.com/olivier-norture/git-flow-ai/issues/572)**). L'idée n°1 (émission OpenTelemetry des métriques d'exécution) est **différée** — non approuvée, suivie sans issue. DSL de graphe, hébergement Azure/Foundry et SDK Go : **rejetés** (grammaire linéaire-with-conditions plus simple et alignée sur le flux d'issues ; positionnement anti-SaaS/anti-lock-in ; aucun signal de demande).

**Tripwires de veille mensuelle** (complètent la veille (j) de la section 9) : toute annonce MAF d'intégration GitHub (issues/PRs) ou d'hébergement d'orchestration ; tout passage de l'ADR Agent Skills du statut *proposé* à *accepted* — chacun rehausserait le niveau de menace.

#### 4.4.7 Carte de positionnement — workflow agentique

Deux axes structurent cet espace : **la place du workflow dans le système** (bibliothèque à embarquer ↔ plateforme encastrée/adoption par dépôt) et **la détermination du contrôle** (conversation/rôles ↔ graphe/pipeline déterministe).

```
                       Pipeline/graphe déterministe
                                   ▲
                                   │
                    ◼ Kestra       │
                      (tâches,    │        ◼ git-flow-ai
                       infra)     │          (workflows YAML
                    ◼ LangGraph   │           multi-agents,
                                   │          transport GitHub)
   Bibliothèque/SDK  ◄─────────────┼────────────►  Plateforme encastrée
   à embarquer              │
                            │   ◼ CrewAI        ◼ gh-aw (expérimental)
                            │   ◼ AutoGen         (single-agent MD)
                                   ▼
                        Conversation / rôles
```

Constats :

1. **Le croisement « plateforme encastrée × graphe déterministe × sémantique agentique » reste non occupé.** Kestra occupe la case déterministe mais sans sémantic agentique ni ancrage GitHub-native ; gh-aw occupe l'ancrage GitHub mais reste single-agent et conversationnel [57][64]. La position *git-flow-ai* demeure distinctive — cohérente avec la carte du segment revue de code (§5.2).
2. **Les frameworks OSS (LangGraph, CrewAI, AutoGen) ne sont pas des challengers directs mais des fournisseurs potentiels** : tous trois peuvent servir de moteur *à l'intérieur* d'une étape d'un workflow *git-flow-ai* ; aucun ne propose le transport GitHub-native + le processus versionné [50][59][60].
3. **La vraie descension possible est la plateforme native** : gh-aw + Copilot dans la plateforme dominante [57][17] — même scénario de menace que la section 8 (« à quelle vitesse », pas « si »).
4. **L'homogénéité du gabarit** : aucune de ces fiches ne remet en cause les six pain points traités en 5.1 — les réponses *git-flow-ai* (processus versionné, délégation, gates) restent valides sur le périmètre élargi [50].

---

## 5. Positionnement & différenciation

### 5.1 Les six pain points et leur réponse

`landscape.md` structure le positionnement autour de six douleurs du marché [50] :

| # | Pain point (constat de marché) | Mécanisme *git-flow-ai* |
|---|---|---|
| 1 | Chaque nouvel outil IA échoue à l'intégration ; l'audit « où ça se place ? » se refait à la main, vit dans quelques têtes | Un outil IA devient **une étape d'un processus défini** (step d'un workflow), inspectable et versionné |
| 2 | Le code généré par IA déplace le goulot vers la revue humaine (cf. section 3.3) | Deux gates de niveau conception au lieu de la revue ligne à ligne ; la revue (`@@review`) est elle-même un agent spécialisé [50] |
| 3 | L'automatisation CI/CD déterministe ne peut pas s'adapter (échec ou avancée aveugle) | La délégation `@@` : une étape qui manque un renseignement ping le bon spécialiste au lieu de faire tomber le pipeline |
| 4 | Le processus d'équipe est une connaissance tribale, non versionnée | Le workflow est **un artefact first-class** : commité, diffable, forkable, partageable |
| 5 | Les assistants IA sont des agents uniques sans délégation | Rôles, roster, et délégation `@@mention` — la compétence centrale de l'équipe d'ingénierie, restituée [50] |
| 6 | Friction de setup + risque de credentials (PAT longs, scopes larges, difficiles à révoquer) | Direction prise : **GitHub App** par défaut (installation par dépôt, permissions fines, jetons scopés et révocables), PAT conservé en fallback [50] |

### 5.2 Carte de positionnement

Deux axes structurent l'espace concurrentiel : **le périmètre automatisé** (une étape ↔ le processus complet) et **la structure d'agents** (agent unique ↔ multi-agents avec délégation).

```
                         Multi-agents + délégation
                                   ▲
                                   │
                                   │   ◼ git-flow-ai
                                   │     (position revendiquée)
                                   │
   Une étape (revue, merge)  ◄────┼────►  Processus complet
                                   │
          ◼ CodeRabbit             │
          ◼ Copilot Code Review    │
          ◼ Graphite/Diamond       │
          ◼ Greptile, Qodo         │
          ◼ Devin/Cognition
                                   ▼
                          Agent unique / sans délégation
```

À la date de cette étude, le quadrant supérieur-droit (processus complet × multi-agents) est **vide d'acteurs identifiés** — c'est le positionnement de *git-flow-ai* [50], et le risque que des entrants déjà financés y descendent (section 8).

### 5.3 Ce que les concurrents font / ne font pas

- **Ils font** : la revue de PRs (CodeRabbit, Copilot, Diamond, Greptile, Qodo) ; l'automatisation d'une fonction du pipeline ; l'exécution autonome de tâches de développement (Devin).
- **Ils ne font pas** (à la date de cette étude, sur la base des sources collectées) : la délégation explicite entre agents de rôles différents ; le versionnement du *processus d'équipe* comme livrable diffable ; les gates d'approbation humaine intégrés à un flux agentique ; la mesure du processus (signaux comme interactions humaines par issue, tokens par issue, taux de re-travail, temps de merge — définitions en cours) [50].
- **Spécificités défensives** : transport GitHub natif (pas de dashboard opaque), distribution zero-copy, configuration agent-writable [50][52].

---

## 6. Modèle économique & coûts

### 6.1 Le modèle discuté : cœur ouvert + couche SaaS

Le modèle économique envisagé dans l'issue #475 est **open-core** [48][50] :

- **Cœur open-source** : le moteur, les définitions d'agents, la délégation `@@`, le format workflow et son exécuteur, le transport GitHub. Self-hosted, runnable avec sa propre clé LLM (adapter pluggable : OpenRouter, modèles locaux, tout provider), zéro dépendance cloud [50].
- **SaaS managé (à venir)** : exploitation hébergée du cœur — routage LLM managé et contrôle de coûts, observabilité, visibilité inter-dépôts des agents, **marketplace de workflows** avec découverte et curation. Couche de distribution et de confort, jamais fourche du cœur [50]. Les variantes de monétisation évoquées dans #475 : templates payants, **pay-per-issue**, accès API [48].

Logique GTM associée : les communautés OSS adhèrent à la **dynamique** plutôt qu'à la complétude — le projet est donc mené en *build-in-public* (un post par gate), avec pour moat affiché **le format workflow + la communauté**, valables publiquement plutôt qu'en interne [48].

### 6.2 Structure de coûts d'inférence réelle

La configuration production actuelle du dépôt documente les prix par modèle en usage [53] (unités de prix non documentées dans `agent-config.yaml` — à recouper avec la facturation OpenRouter) :

| Modèle | Provider | Prix entrée | Prix sortie | Rôle typique |
|---|---|---|---|---|
| `deepseek-v4-pro` | OpenRouter | 2,0 | 8,0 | Travail de raisonnement exigeant |
| `deepseek-v4-flash` | OpenRouter | 0,12 | 0,18 | Tâches courantes à bas coût |
| `qwen-3-6-30b` (thinking activé) | vLLM auto-hébergé | 0,35 | 0,40 | Raisonnement sur infrastructure propre |
| `claude-sonnet-5` | OpenRouter | non documenté | non documenté | configuré, prix non relevé [53] |

**Lecture économique** : l'écart de coût entre un modèle « pro » et un modèle « flash » atteint un facteur ~16–44 par token selon entrée/sortie [53]. Comme chaque agent est routable vers un modèle différent par simple configuration YAML [52], le **coût marginal par issue est directement pilotable** : taches de routine sur modèles flash/locaux, gates et spec sur modèles forts. C'est un argument de **marge** pour la future couche SaaS (le SaaS facture le confort, pas l'inférence) et un argument de **scalabilité** pour le self-hosted (un vLLM local amorti sur une équipe change la structure de coûts). À noter : l'architecture elle-même réduit la consommation — un agent de contexte à lecture seule sur LLM low-cost, plutôt que d'alimenter tous les agents au meilleur modèle [52].

### 6.3 Hypothèses non validées

Le pricing cible de *git-flow-ai* (grille SaaS, prix de la marketplace) **n'est pas documenté** dans les sources collectées [48] : il n'existe pas encore de grille publique. C'est un point à construire dans l'approfondissement (section 9, point d) en s'appuyant sur les benchmarks de section 4.1 (Free/~$20–30 par utilisateur/mois côté review assistée [14][15]).

---

## 7. Traction, maturité & plan de release

### 7.1 Traction : chiffres bruts

Données API GitHub relevées le 2026-08-28 [54] :

| Indicateur | Valeur |
|---|---|
| Date de création du dépôt | 2026-05-29 (~3 mois d'existence) |
| Stars / Forks / Watchers | 0 / 0 / 0 |
| Issues ouvertes | 79 |
| Licence | Apache-2.0 (voir annexe A sur le point licence) |

Lecture honnête : **aucune traction externe mesurable à ce jour**. Le volume d'issues (79) reflète un rythme de développement élevé et un usage intensif en dogfooding [48], pas une adoption. La stratégie de lancement assume cet état et le transforme en méthode : construire en public, publier la progression gate par gate, viser la demande organique (« fork this workflow ») plutôt qu'un lancement type big reveal [48][50].

### 7.2 Maturité produit

- **Pré-alpha** : le produit n'est pas encore publié sous forme installable par des équipes externes ; la PR #481 (documents *landscape*) constitue le **Gate 0** — le dépôt comme page d'atterrissage, publié avant la plupart du moteur [48][49].
- **Maturité d'ingénierie** : architecture DDD par feature, refactorings itératifs documentés, discipline de revue multi-agents — visible dans l'historique du dépôt (centaines de PRs orchestrées par les agents eux-mêmes), ce qui double de démonstration de l'outil [52].
- **Dogfooding** : l'ensemble de ce projet est développé par son propre workflow — argument de crédibilité fort mais qui ne remplace pas des utilisateurs tiers (section 9, point g).

### 7.3 Plan de release en quatre gates

Plan de lancement v4 de l'issue #475 (2026-08-21) [48] :

| Gate | Contenu | Critère |
|---|---|---|
| **0 — Repo-as-landing-page** | Publier la vision et le positionnement avant le moteur (documents *landscape*, PR #481) | Le dépôt parle de lui-même |
| **1 — Alpha** | Un aller-retour de délégation `@@` réel entre deux agents sur une vraie issue + démo vidéo ; posts Show HN / r/devtools / dev.to | Premiers retours externes |
| **2 — Beta** | Workflow flagship *spec-driven* end-to-end avec ses deux gates humains ; **freeze du format YAML v1 = contrat public** | Le format durcit sous les yeux du public plutôt qu'en silence |
| **3 — Release 0.1** | Tag stable, format documenté comme contrat public, workflows robustes assez pour être forkés | « Fork this workflow » devient la demande naturelle |

Le format public v1 est délibérément gelé tard mais de façon visible : le schéma YAML et la sémantique des gates humains « se valideront mieux durcie sous les yeux du public à la Gate 2 que silencieusement dans votre dépôt » [48]. Le nommage du workflow flagship est traité comme stratégique — « c'est le premier artefact partageable et la graine de votre marketplace » [48].

---

## 8. Analyse critique (forces / faiblesses / opportunités / menaces)

Analyse volontairement non flatteuse — un document transmissible doit pouvoir survivre au scepticisme du lecteur.

### Forces

- **Différenciation structurelle, pas incrémentale** : la délégation inter-agents et le processus versionné ne se réduisent pas à une fonction de plus sur un outil de revue ; c'est un autre objet (un processus, pas une fonction) [50].
- **Distribution zero-copy et coût d'adoption minimal** : une référence YAML + deux secrets ; mise à jour transparente depuis le dépôt amont [52].
- **Coûts d'inférence pilotables** par configuration (section 6.2) [53].
- **Transport GitHub natif** : zéro nouvelle surface à installer côté utilisateur ; la filature d'issue est déjà l'endroit où l'équipe travaille [50].
- **Vision claire et documentée publiquement** (gates, moat, GTM) — rare à 3 mois d'existence, et vérifiable par les parties prenantes [48][50].

### Faiblesses

- **Traction nulle** : 0 star / 0 fork / 0 watcher au 2026-08-28 [54]. Aucune preuve d'adoption tierce.
- **3 mois d'existence, produit en pré-alpha** : rien de publiable pour des équipes externes avant la Gate 1 [48].
- **Dépendance à un seul mainteneur** visible ; la communauté n'existe pas encore, alors que le moat revendiqué est « format + communauté » [48].
- **Runner self-hosted requis** : friction d'onboarding réelle pour les équipes sans infrastructure d'Actions self-hosted [52].
- **Signaux de mesure du processus encore « en cours de définition »** : la promesse de measurability n'est pas encore démontrée par des chiffres [50].

### Opportunités

- **Crise de la revue documentée** (section 3.3) : le problème est urgent, chiffré, et ressenti par les équipes qui adoptent l'IA [30][34].
- **Segment vierge** : le quadrant « processus complet × multi-agents » n'a pas d'occupant identifié ; le capital investi ailleurs (review unitaire) valide le thème sans occuper cet espace [1][4][8][11].
- **Écosystème OpenHands** : plateforme ouverte de coding agents avec offre cloud, sur laquelle le produit s'appuie — partenariat/natural fit possible [44][45][46].
- **Effet réseau du format** : chaque workflow forké par une équipe externe renforce le contrat public v1 et alimente la future marketplace — le produit devient meilleur à chaque adoption [48][50].
- **Diversité des modèles IA** : la guerre des prix des LLM [53] abaisse le coût marginal du produit plus vite que ses concurrents purement SaaS, pour qui l'inférence est une ligne de coût fixe.

### Menaces

- **Descension des entrants capitalisés** : CodeRabbit (« Agentic Change Management » [2]) ou une plateforme Copilot élargissant sa revue à l'orchestration multi-étapes pourrait occuper le segment en moins d'un an de développement [11][19].
- **Convergence des plateformes natives** : GitHub détient déjà le transport (issues/actions) ; la question est « à quelle vitesse », pas « si ».
- **Dépendance à deux plates-formes** (GitHub, OpenHands) : changement de politique, de pricing ou de roadmap de l'une d'elles impacte directement le produit [44][52].
- **Risque d'accueil tiède du multi-agents** : si la méta-analyse de la productivité IA confirme les résultats METR [30], les équipes pourraient devenir sélectives sur *tout* l'outillage agentique, pas seulement la revue.
- **Fenêtre de tir limitée par définition** : les fenêtres de segments vierges dans l'IA se ferment vite (CodeRabbit : de la Série B à $1,5 Md en moins d'un an [1]).

**Synthèse critique** : le projet a un vrai différenciateur sur un vrai pain point, dans une fenêtre qui se ferme. Son principal actif (le format) ne prend de valeur que s'il est adopté ; son principal passif (zéro traction) ne se résorbe qu'en exécutant les gates 1-2 avec des équipes tierces réelles. L'étude est donc conditionnelle : ce document décrit une **opportunité**, pas une trajectoire acquise.

---

## 9. Comment approfondir cette étude

Actions concrètes, classées par effort croissant. Les points (a)–(e) visent à transformer les signaux secondaires en faits ; les points (f)–(g) visent à mesurer la demande réelle.

1. **(a) Entretiens qualitatifs (priorité 1).** Recruter 8–15 TLMs / dev leads d'équipes de 5 à 50 développeurs qui utilisent déjà un outil IA de génération. Questions-cadres : quel outil est installé, où le pipeline stagne aujourd'hui, qui paie quoi, quels critères déclencheraient l'essai d'un outil de processus (vs d'un outil de revue), et quel est le critère d'abandon après 30 jours. Livrable : persona + critères de décision documentés, à comparer avec l'hypothèse « crise de la revue » de la section 3.3.
2. **(b) Analyse de sentiment et mining de communautés.** Balayage structuré de Reddit (r/devtools, r/ExperiencedDevelopers), Hacker News, dev.to : requêtes « AI code review bottleneck », « too many PRs to review », « Copilot review», « Devin workflow ». En parallèle, **mining des issues GitHub des concurrents** (CodeRabbit, Greptile, Graphite) : les issues « feature request» non traitées sont le catalogue gratuit de ce que le marché voudrait et que le segment financé ne livre pas. Livrable : matrice d'exigences non couvertes.
3. **(c) Recoupement des chiffres secondaires avec les sources primaires.** Les données Faros AI (+441,5 % / « 98 % / +91 % » [34][35]) et LinearB (2,5× / 5× [34]) proviennent de blogs secondaires : contacter les auteurs des rapports, récupérer les méthodologies (taille d'échantillon, période, définition de « review time ») et ne citer dans les versions futures de cette étude que les chiffres validés.
4. **(d) Benchmark pricing ligne à ligne.** Scraper les pages officielles (CodeRabbit [14], Copilot [18], Devin [22]) et produire un tableau : ce qui est inclus par tier, limites de PRs/caractères, pricing par utilisateur vs par consommation, prix enterprise. Comparer avec la structure de coûts de la section 6.2 pour définir une grille SaaS défendable avant la Gate 2 [48].
5. **(e) Extraction des rapports de marché.** Lire intégralement les rapports agentic-AI [39][40][41] et consigner les valeurs TAM/SAM/CAM, les hypothèses de croissance et les segments « developer productivity » ; remplacer l'affirmation qualitative de la section 3.1 par des chiffres sourcés.
6. **(f) Enquête de willingness-to-pay avant la beta.** Court questionnaire (200–500 répondants via les communautés ciblées en (b)) avec présentation du workflow flagship et du coût d'onboarding (self-hosted runner, clés) ; mesurer le taux de déclaration d'essai conditionnelle et le prix d'indifférence. À mener idéalement entre les Gates 1 et 2, quand la démo existe [48].
7. **(g) Études de cas dès les Gates 1–2.** Intégrer 3 à 5 équipes externes en conditions réelles sur délégation `@@` ; instrumenter les signaux de la section 2.4 (interactions humaines par issue, tokens par issue, temps de merge, re-travail) et publier les résultats — ce qui transforme la promesse de *measurability* [50] en preuve, et fournit les premiers artefacts partageables de la future marketplace [48].
8. **(h) Veille concurrentielle trimestrialisée.** Suivi systématique : financements et pivots des acteurs de la section 4.1 (en particulier le pivot « Agentic Change Management » de CodeRabbit [2]), changelogs Copilot [19], publications OpenHands [46]. Livrable mensuel léger : « qui est entré dans notre quadrant ».
9. **(i) Audit d'absence de concurrent OSS.** Confirmer ou infirmer le constat de la section 4.3 par requêtes ciblées GitHub (topics : `multi-agent`, `github-actions`, `agentic-workflow`) et inspection des stars croissantes sur 6 mois.
10. **(j) Veille renforcée du segment workflow agentique.** Trois événements à surveiller mensuellement, car chacun redessinerait la carte de la section 4.4.7 : (1) le passage de **gh-aw** du statut expérimental GitHub Next à un produit stable — en particulier l'éventuelle levée de la contrainte single-job (le seul vrai chemin de concurrence directe) [57][58] ; (2) l'issue [langgraph #8026](https://github.com/langchain-ai/langgraph/issues/8026) « approbation first-class » — sa livraison rapprocherait LangGraph du pattern gate humain revendiqué par *git-flow-ai* [59] ; (3) la consolidation **AutoGen → Microsoft Agent Framework** et son positionnement éventuel sur l'orchestration multi-agents [62] — désormais concrétisée en fiche dédiée 4.4.6, avec deux tripwires explicites : toute annonce d'intégration GitHub (issues/PRs) ou d'hébergement d'orchestration par MAF, et le passage de l'ADR Agent Skills au statut *accepted* [65][66]. En outre, refermer les limitations documentées en 4.4.4/4.4.5 : pricing détaillé de Kestra et licence/adhésion de gh-aw, non documentés au moment de la collecte.

---

## 10. Index des sources

Toutes les sources web ont été collectées et vérifiées le **2026-08-28** via recherche web (SearXNG). Types : **P** = source primaire (éditeur officiel, étude, annonce de l'acteur concerné) ; **S** = source secondaire (presse, blog citant une source primaire). Les sources secondaires portant des chiffres clés sont marquées **(à recouper)**.

### 10.1 Concurrence & financements

- **[1]** P — Reuters : « AI code-review platform CodeRabbit valued at $1.5 billion in latest funding round » (2026-08-12) — https://www.reuters.com/technology/ai-code-review-platform-coderabbit-valued-15-billion-latest-funding-round-2026-08-12/
- **[2]** P — BusinessWire : « CodeRabbit Raises $143 Million at $1.5 Billion Valuation and Introduces Agentic Change Management » (2026-08-12) — https://www.businesswire.com/news/home/20260812311754/en/CodeRabbit-Raises-$143-Million-at-$1.5-Billion-Valuation-and-Introduces-Agentic-Change-Management
- **[3]** S — TechStartups : « CodeRabbit Raises $143M at $1.5B Valuation to Manage the AI-Generated Code Explosion » (2026-08-12) — https://techstartups.com/2026-08-12/coderabbit-raises-143m-at-1-5b-valuation-to-manage-the-ai-generated-code-explosion/
- **[4]** P — Blog officiel Graphite : « Series B & Diamond launch » — https://staging-graphite-splash.vercel.app/blog/series-b-diamond-launch
- **[5]** S — PlushCap (miroir du blog Graphite) : « Graphite raises $52M and launches AI code review agent Diamond » — https://www.plushcap.com/content/graphite/blog/graphite-graphite-raises-52m-and-launches-ai-code-review-agent-diamond
- **[6]** S — OpenTools : « Graphite AI supercharging code reviews with $52 million boost » — https://opentools.ai/news/graphite-ai-supercharging-code-reviews-with-dollar52-million-boost
- **[7]** P — Site produit Graphite : « Code review for the age of AI » — https://www.graphite.com/
- **[8]** S — Tech Funding News : « AI startup Greptile eyes $180M valuation to disrupt future of AI code review » — https://techfundingnews.com/ai-startup-greptile-eyes-180m-valuation-to-disrupt-future-of-ai-code-review/
- **[9]** S — All About AI : « Greptile bags $30M to boost future of AI code review » — https://www.allaboutai.com/ai-news/greptile-bags-30m-to-boost-future-of-ai-code-review/
- **[10]** S — TechShotsApp : « Benchmark to lead Series A for Greptile, valued at $180M » (détails : alumni YC, fondée 2023 par Dasksh Gupta) — https://www.techshotsapp.com/business/benchmark-to-lead-series-a-for-greptile-valued-at-180m
- **[11]** S — Pick-Right : « Cognition $25B valuation — Windsurf & Devin » (2026-05-26) — https://pick-right.com/news/cognition-25b-valuation-windsurf-devin-2026-05-26/
- **[12]** S — Idlen.io : « Cognition Devin $25 billion valuation, Windsurf vibe coding » (mention « ARR doubled ») — https://www.idlen.io/news/cognition-devin-25-billion-valuation-windsurf-vibe-coding-april-2026/
- **[13]** S — Tech Funding News : « Cognition AI $25B valuation funding talks, Devin software engineer » — https://techfundingnews.com/cognition-ai-25b-valuation-funding-talks-devin-software-engineer/

### 10.2 Pricing

- **[14]** P — CodeRabbit : page pricing officielle — https://www.coderabbit.ai/pricing
- **[15]** S — CostBench : comparatif « CodeRabbit Free–$30/User plans compared » **(à recouper)** — https://costbench.com/software/ai-code-review/coderabbit/
- **[16]** S — dev.to (R. Singh) : « CodeRabbit Pricing in 2026: Free Tier, Pro Limits and Enterprise Costs » **(à recouper)** — https://dev.to/rahulxsingh/coderabbit-pricing-in-2026-free-tier-pro-limits-and-enterprise-costs-1pc4
- **[17]** P — Documentation officielle GitHub : « Copilot Code Review — concepts » — https://docs.github.com/en/copilot/concepts/agents/code-review
- **[18]** P — GitHub : pages de plans Copilot (pricing par *premium requests*) — https://github.com/features/copilot/plans
- **[19]** P — GitHub Changelog (2025-12-17) : « Copilot code review now available for organization members without a license » — https://github.blog/changelog/2025-12-17-copilot-code-review-now-available-for-organization-members-without-a-license/
- **[20]** S — andrew.ooo : analyse GitHub Copilot (2026) — https://andrew.ooo/tools/ai-coding/github-copilot/
- **[21]** S — Future Stack Reviews : « GitHub Copilot pricing » — https://future-stack-reviews.com/github-copilot-pricing/
- **[22]** P — Devin : page pricing officielle — https://devin.ai/pricing
- **[23]** S — VP0 : « Devin AI pricing plans 2026 » (modèle ACU : Core $20/mois + $2,25/ACU ; Team $500/mois incl. 250 ACU @ $2,00) **(à recouper)** — https://vp0.com/blogs/devin-ai-pricing-plans-2026
- **[24]** S — UseCarly : « Devin pricing » — https://www.usecarly.com/blog/devin-pricing/
- **[25]** S — AIToolPick : « Devin pricing 2026 » — https://aitoolpick.org/blog/devin-pricing-2026/
- **[26]** S — Greptile Content Library : « Best AI code review tools » — https://www.greptile.com/content-library/best-ai-code-review-tools
- **[27]** S — Belreos : « Best AI code review tools 2026 » — https://belreos.com/blog/best-ai-code-review-tools-2026
- **[28]** S — FuturePicker : « CodeRabbit vs Qodo vs Greptile AI code review 2026 » — https://futurepicker.com/en/coderabbit-vs-qodo-vs-greptile-ai-code-review-2026-en/
- **[29]** S — Qodex : « Best AI code review tools » — https://qodex.ai/blog/best-ai-code-review-tools

### 10.3 Données de marché

- **[30]** P — METR : « Early-2025 AI experienced OS dev study » (RCT, 2025-07-10 ; 16 devs, 246 tâches ; +19 % de temps avec IA) — https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/
- **[31]** P — Préprint arXiv de l'étude METR (2507.09089) — https://arxiv.org/abs/2507.09089
- **[32]** S — ScienceBlog : couverture de l'étude METR (« 19 % slower … estimated 20 % faster ») — https://scienceblog.com/t-a-randomized-trial-by-metr-found-that-experienced-developers-completed-real-coding-tasks-19-slower-when-allowed-to-use-ai-tools-yet-afterwards-they-estimated-on-average-that-ai-had-made-them-20-fast/
- **[33]** S — Particula : « AI coding tools: the developer productivity paradox » — https://particula.tech/blog/ai-coding-tools-developer-productivity-paradox
- **[34]** S — FlowVerify : « AI code review bottleneck 2026 data » (Faros AI : temps de revue médian +441,5 % vs throughput +33,7 % ; LinearB 8,1 M PRs : PRs IA ~2,5× plus grandes, attente ~5×) **(à recouper)** — https://www.flowverify.co/blog/ai-code-review-bottleneck-2026-data
- **[35]** S — ScreenCLI : « AI code review bottleneck » (chiffres alternatifs Faros « +98 % PRs mergées, +91 % temps de revue ») **(à recouper)** — https://screencli.sh/blog/ai-code-review-bottleneck
- **[36]** S — AgentMarketCap : « Faros AI productivity paradox, team delivery slowdown » **(à recouper)** — https://agentmarketcap.ai/blog/2026-04-23/faros-ai-productivity-paradox-team-delivery-slowdown
- **[37]** S — Codexical : « AI code review bottleneck » — https://www.codexical.com/posts/2026-05-13-ai-code-review-bottleneck
- **[38]** S — Votazz : « AI code review bottleneck » (« 61 % of AI pull requests never see human eyes ») **(à recouper)** — https://votazz.co/2026-07-07/ai-code-review-bottleneck/
- **[39]** S — The Business Research Company : « Agentic AI Tools — Global Market Report 2026–2030 » — https://www.thebusinessresearchcompany.com/report/agentic-ai-tools-global-market-report
- **[40]** S — MarketsandMarkets : « AI Agents Market » — https://www.marketsandmarkets.com/Market-Reports/ai-agents-market-15761548.html
- **[41]** S — Analytics Insight : « Agentic AI market forecast 2030 » — https://www.analyticsinsight.net/reports/agentic-ai-market-forecast-2030-analytics-insight
- **[42]** S — IBM : « What is agentic AI ? » (définition de référence) — https://www.ibm.com/think/topics/agentic-ai
- **[43]** S — Wikipédia : « AI agent » — https://en.wikipedia.org/wiki/AI_agent

### 10.4 Écosystème

- **[44]** P — OpenHands : « The Open Platform for Cloud Coding Agents » — https://www.openhands.dev/
- **[45]** P — Dépôt OpenHands — https://github.com/OpenHands/OpenHands
- **[46]** P — Documentation OpenHands (introduction, offre Cloud managée) — https://docs.openhands.dev/overview/introduction
- **[47]** P — GitHub : page d'accueil (« 150+ million people… 420+ million repositories ») — https://github.com/

### 10.5 Sources internes et mesures directes

- **[48]** Issue interne **#475** (plan de lancement v4, 2026-08-21 : gates, GTM, modèle économique, moat) — https://github.com/olivier-norture/git-flow-ai/issues/475
- **[49]** PR interne **#481** (documents *landscape*, Gate 0) — https://github.com/olivier-norture/git-flow-ai/pull/481
- **[50]** `landscape.md` (dépot racine, ajouté par la PR #481) — vision, 6 pain points, solution
- **[51]** `landscape_commercial.md` (dépot racine, branche de la PR #481) — version narrative commerciale du même constat *(supprimé depuis : le contenu a été consolidé dans `landscape.md`)*
- **[52]** `README.md` (dépot racine) — architecture 5 agents, usage, distribution zero-copy, configuration 3 couches
- **[53]** `agent-config.yaml` (dépot racine) — providers, modèles, prix d'inférence par modèle
- **[54]** Métadonnées du dépôt via API GitHub, relevées le 2026-08-28 — https://github.com/olivier-norture/git-flow-ai (création 2026-05-29 ; 0 star / 0 fork / 0 watcher ; 79 issues ouvertes ; licence Apache-2.0)
- **[55]** Recherche web « multi-agent GitHub workflow automation open source » (2026-08-28, SearXNG) : aucun concurrent OSS direct exploitable identifié (signal faible, moteur de qualité variable) — cf. caveat de la section 4.3

### 10.6 Outils de workflow agentique (section 4.4)

Analyse interne consolidée (issues #227, #291, #318, #319 ; PR #485 — cf. https://github.com/olivier-norture/git-flow-ai), complétée par le recoupement direct des sources primaires ci-dessous, vérifiées le 2026-08-28.

- **[56]** P — API GitHub : métadonnées des dépôts concernés, relevées le 2026-08-28 (stars / forks / licence / date de création) — langchain-ai/langgraph (40 606 ⭐ / 6 839 forks, MIT, créé 2023-08) — crewAIInc/crewAI (57 723 ⭐ / 8 269 forks, MIT, créé 2023-10) — microsoft/autogen (60 668 ⭐ / 9 160 forks, CC-BY-4.0, créé 2023-08) — kestra-io/kestra (27 944 ⭐ / 2 952 forks, Apache-2.0, créé 2019-08)
- **[57]** P — GitHub Agentic Workflows (gh-aw) : documentation officielle du projet — home https://github.github.com/gh-aw/ ; README du dépôt github/gh-aw (multi-moteur Copilot/Claude/Codex/Gemini, read-only par défaut, `safe-outputs`, compilation `.lock.yml`) ; references workflow-format/subagents/workflow-patterns (contrainte single-job, sub-agents inline héritant du contexte parent) ; CLI `gh aw` (new/compile/logs/audit)
- **[58]** P — GitHub Next : page projet « Agentic Workflows » — https://githubnext.com/projects/agentic-workflows/ (statut expérimental, « multi-engine by design »)
- **[59]** P — LangGraph : documentation officielle — https://langchain-ai.github.io/langgraph/ (runtime stateful agents, graphe de machine à états Pregel, checkpointers, `interrupt()`/`Command(resume=…)` pour human-in-the-loop, LangSmith observability & deployment) ; tracker du dépôt — issue [langgraph #8026](https://github.com/langchain-ai/langgraph/issues/8026) (demande d'approbation first-class, ouverte)
- **[60]** P — CrewAI : documentation officielle — https://docs.crewai.com/en/introduction (Agents rólés, Tasks, Crews, processes séquentiel/hiérarchique/consensuel, `wait_for_input()`)
- **[61]** S — Comparatifs et retours de terrain 2025–2026 sur CrewAI vs LangGraph vs AutoGen (fiabilité du pattern manager-worker, limites d'observabilité, courbes d'apprentissage) — consolidation des analyses internes issues #227/#318 recoupées avec blogs techniques cités dans ces issues (à recouper point par point avant citation externe)
- **[62]** P — Microsoft AutoGen : dépôt officiel — https://github.com/microsoft/autogen (framework de conversations multi-agents ; annonces de consolidation vers le Microsoft Agent Framework ; licence du dépôt CC-BY-4.0)
- **[63]** P — Kestra : site et documentation officiels — https://kestra.io/ et https://kestra.io/docs/ (plateforme event-driven d'orchestration & scheduling, flows YAML déclaratifs, 1 600+ plugins, UI web DAG temps réel, Apache-2.0, dépôt https://github.com/kestra-io/kestra)
- **[64]** P — Kestra : documentation des composants de workflow — flows/tasks (langage d'expressions `${inputs.*}`/`${outputs.*}`, `ForEach`/`Switch`/`Parallel`), retries avec backoff, timeouts, checkpointing/idempotence, task `Pause` (human-in-the-loop), self-hosting Docker/Kubernetes — https://kestra.io/docs/workflow-components/flow ; analyse comparative interne issue #319 (DSL, exécution, état, plugins, déploiement)
- **[65]** P — Microsoft Agent Framework : dépôt officiel — https://github.com/microsoft/agent-framework (SDK Python/.NET pour agents et workflows multi-agents production-grade ; workflows graphe — séquentiel/concurrent/handoff/group collaboration —, durabilité, checkpointing/time-travel, OpenTelemetry natif ; agents déclaratifs YAML `declarative-agents/` dont `DeepResearch.yaml` (pattern Magentic) ; ADR 0037 Agent Skills au statut *proposé* au 2026-03-23 — divulgation progressive `load_skill`/`read_skill_resource`/`run_skill_script` ; ADR 0006 user-approval au statut *accepted* — approbations d'appels de fonctions locales/MCP ; matrice d'hébergement Azure Functions/conteneurs/Durable Task/Foundry-hosted/A2A/MCP ; recherche de code le 2026-09-11 : aucun transport GitHub-issues, Copilot uniquement en tant que provider LLM) ; Go SDK séparé — https://github.com/microsoft/agent-framework-go ; extension durable — https://github.com/microsoft/agent-framework-durable-extension ; métadonnées API GitHub relevées le 2026-09-11 : 13 474 ⭐ / 2 300 forks / 625 issues ouvertes, MIT, créé 2025-04-28, releases récentes python-1.18.0 (2026-09-10) / dotnet-1.20.0 (2026-08-31)
- **[66]** P — Microsoft Agent Framework : documentation MS Learn et blog officiel — https://learn.microsoft.com/en-us/agent-framework/ (overviews, quick starts, guides utilisateurs ; guides de migration depuis Semantic Kernel et AutoGen — https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-autogen) — confirme la consolidation AutoGen + Semantic Kernel en MAF ; blog — https://devblogs.microsoft.com/agent-framework/

---

## Annexe A — Points ouverts

**Tension licence.** Le dépôt est actuellement publié sous **Apache-2.0** [54]. Dans l'issue #475, une inclinaison vers **AGPL v3** pour le cœur est discutée, argumentée par `landscape.md` comme moyen de « garder le moteur libre tout en empêchant la re-service fermée de copies non modifiées », avec la décision finale annoncée comme pendante [48][50]. Cette question est **à trancer avant le Gate 0** et conditionne directement le modèle SaaS/marketplace de la section 6.1 : le choix entre une licence permissive (facilite l'adoption par les entreprises et la commercialisation de la couche managée) et une licence copyleft (protège le cœur contre la captation par des revendeurs fermés) a des implications juridiques et commerciales opposées. Ce document se contente de poser le point de façon factuelle, sans recommandation.

*Fin du document.*