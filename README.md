# Technogym (Mywellness) pour Home Assistant

Intégration personnalisée qui relie ton compte **Technogym / Mywellness** (salle de sport) à Home Assistant : séances, MOVEs, calories, indice Movergy, cours réservés, et réservation de cours depuis HA.

> ⚠️ **API non officielle.** Technogym ne propose pas d'API personnelle. L'intégration utilise la même API que l'application web officielle (mywellness.com). Si Technogym change son fonctionnement, il faudra adapter `api.py`. Mise à jour par défaut toutes les 30 min, volontairement espacée.

## Installation

**Via HACS (dépôt privé ou public)**
1. Pousse ce dossier dans un dépôt GitHub (ex. `ha-technogym-kerherve`).
2. HACS → ⋮ → *Dépôts personnalisés* → URL du dépôt, catégorie **Intégration**.
3. Télécharge **Technogym (Mywellness)** puis redémarre HA.

**Manuel**
1. Copie `custom_components/technogym` dans `/config/custom_components/` (Studio Code Server ou Samba).
2. Redémarre HA.

**Configuration** : Paramètres → Appareils et services → *Ajouter une intégration* → **Technogym** → email + mot de passe de l'appli Technogym. Si le compte est rattaché à plusieurs clubs, l'assistant demande lequel suivre.

Options (bouton *Configurer*) : fréquence (15–240 min), jours d'historique (7–120), jours de planning des cours (0–21).

## Entités créées

Appareil « Technogym Mael » (les `entity_id` exacts dépendent de la langue de HA au moment de l'ajout).

| Entité | Contenu |
|---|---|
| Indice Movergy | Indice d'activité Mywellness |
| MOVEs aujourd'hui / semaine / mois | Cumul des MOVEs (semaine = depuis lundi) |
| Calories aujourd'hui / semaine | kcal |
| Temps d'entraînement semaine | minutes |
| Séances cette semaine / ce mois | nombre ; attribut `seances` = détail |
| Dernière séance | horodatage ; attributs nom, durée, kcal, MOVEs, efficacité |
| Nom dernière séance | attribut `activites` = 10 dernières activités |
| Jours depuis la dernière séance | 0 = aujourd'hui |
| Prochain cours réservé | horodatage ; attributs cours, coach, salle, `reservations` |
| Cours réservés | nombre (diagnostic) |
| Entraîné aujourd'hui | binaire |
| Cours aujourd'hui | binaire |
| Calendrier « Entraînements » | ✅ séances faites + 🏋️ cours réservés + ⏳ liste d'attente |

## Événement

`technogym_new_activity` est émis à chaque nouvelle activité détectée (pas au démarrage).  
Données : `id`, `debut`, `nom`, `type`, `duree_min`, `moves`, `calories`, `efficacite`, `conformite`, `exercices`, `config_entry_id`.

## Actions (services)

| Action | Rôle |
|---|---|
| `technogym.list_classes` | Renvoie le planning (`days`, `only_bookable`) avec `class_id` + `partition_date` |
| `technogym.book_class` | Réserve (`class_id`, `partition_date` AAAAMMJJ) → `result` : `Booked`, `UserAlreadyBooked`, `UserAddedToWaitingList`, `PlaceNotAvailable`… |
| `technogym.unbook_class` | Annule → `UnBooked`, `TooLate`… |
| `technogym.refresh` | Mise à jour immédiate |

## Exemples d'automatisations

### Résumé de séance via ADRIA
```yaml
alias: Technogym — Résumé de séance
mode: queued
triggers:
  - trigger: event
    event_type: technogym_new_activity
actions:
  - action: script.ia_notifier
    data:
      urgence: normale
      titre: "🏋️ Séance enregistrée"
      message: >-
        {{ trigger.event.data.nom }} : {{ trigger.event.data.duree_min }} min,
        {{ trigger.event.data.calories }} kcal, {{ trigger.event.data.moves }} MOVEs.
        Semaine : {{ states('sensor.technogym_mael_seances_cette_semaine') }} séance(s).
```

### Suggestion si pas de séance depuis 4 jours (suggestion seulement)
```yaml
alias: Technogym — Relance après 4 jours sans séance
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.technogym_mael_jours_depuis_la_derniere_seance
    above: 3
actions:
  - action: script.ia_suggest_batch
    data:
      titre: "Sport"
      message: >-
        {{ states('sensor.technogym_mael_jours_depuis_la_derniere_seance') }} jours
        sans séance Technogym.
      source: technogym
```

### Rappel avant un cours réservé
```yaml
alias: Technogym — Rappel cours dans 1 h
triggers:
  - trigger: calendar
    event: start
    offset: "-1:00:00"
    entity_id: calendar.technogym_mael_entrainements
conditions:
  - condition: template
    value_template: "{{ trigger.calendar_event.summary.startswith('🏋️') }}"
actions:
  - action: script.ia_notifier
    data:
      urgence: normale
      titre: "Cours dans 1 h"
      message: "{{ trigger.calendar_event.summary[2:] }} — {{ trigger.calendar_event.location or '' }}"
```

### Lister les cours réservables (outils, ou pour ADRIA)
```yaml
action: technogym.list_classes
data:
  days: 3
  only_bookable: true
response_variable: planning
```

## Dépannage

Ajoute dans `configuration.yaml` pour voir les échanges :
```yaml
logger:
  logs:
    custom_components.technogym: debug
```
- **Mot de passe refusé** : HA ouvre automatiquement une demande de reconnexion.
- **Compte créé via Google/Facebook** : définis d'abord un mot de passe Mywellness (mywellness.com → mot de passe oublié), l'intégration n'accepte que email + mot de passe.
- **Cours vides** : le club n'utilise peut-être pas la réservation Mywellness ; mets « jours de planning » à 0.

## Crédits

Endpoints documentés par les projets open source *health_mcp/technogym* (MIT), *CrowdSalat/medi-bucher* et *AeonDave/myWellnezz*.
