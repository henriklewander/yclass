/*
 * yclass-card: one student's coming school day, lunch, and homework/tests.
 *
 *   type: custom:yclass-card
 *   device: <device id of the student>   # picked in the visual editor
 *   max_items: 10                        # optional
 *
 * Served and loaded by the yclass integration itself (see __init__.py), so
 * there is no dashboard resource to add. All "which day" logic (today until
 * 17:00, then the next school day) lives in the integration's schoolday sensor;
 * the card only shows what that sensor says.
 */

const VERSION = new URL(import.meta.url).searchParams.get("v") || "dev";
const WEEKDAYS = ["sön", "mån", "tis", "ons", "tor", "fre", "lör"];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// "2026-10-23" or "2026-10-23T08:10" -> local Date, without any time zone shifting.
function parseLocal(iso) {
  const [d, t = "00:00"] = String(iso).split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min] = t.split(":").map(Number);
  return new Date(y, m - 1, day, h, min);
}

const isoDate = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

function dayLabel(iso) {
  const date = parseLocal(iso);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const short = `${WEEKDAYS[date.getDay()]} ${date.getDate()}/${date.getMonth() + 1}`;
  if (isoDate(date) === isoDate(today)) return `Idag · ${short}`;
  if (isoDate(date) === isoDate(tomorrow)) return `Imorgon · ${short}`;
  return short;
}

function shortDay(iso) {
  const date = parseLocal(iso);
  const today = isoDate(new Date());
  const tomorrow = isoDate(new Date(Date.now() + 86400000));
  if (isoDate(date) === today) return "idag";
  if (isoDate(date) === tomorrow) return "imorgon";
  return `${WEEKDAYS[date.getDay()]} ${date.getDate()}/${date.getMonth() + 1}`;
}

// "Köttbullar med potatis (Fläsk, Nöt)" -> ["Köttbullar med potatis", "Fläsk, Nöt"]
function splitDish(dish) {
  const m = String(dish).match(/^(.*?)\s*\(([^()]*)\)\s*$/);
  return m ? [m[1], m[2]] : [dish, ""];
}

// The yclass entities of one device, keyed "sensor.homework", "sensor.schoolday", ...
function studentEntities(hass, config) {
  let deviceId = config.device;
  if (!deviceId && config.entity) deviceId = hass.entities?.[config.entity]?.device_id;
  const found = {};
  if (!deviceId) return found;
  for (const ent of Object.values(hass.entities || {})) {
    if (ent.device_id === deviceId && ent.platform === "yclass" && ent.translation_key) {
      found[`${ent.entity_id.split(".")[0]}.${ent.translation_key}`] = ent.entity_id;
    }
  }
  return found;
}

class YclassCard extends HTMLElement {
  static getConfigForm() {
    return {
      schema: [
        { name: "device", required: true, selector: { device: { integration: "yclass" } } },
        { name: "max_items", selector: { number: { min: 1, max: 30, mode: "box" } } },
      ],
      computeLabel: (s) => ({ device: "Elev", max_items: "Max antal läxor och prov" })[s.name],
    };
  }

  static getStubConfig(hass) {
    const ent = Object.values(hass.entities || {}).find((e) => e.platform === "yclass" && e.device_id);
    return { device: ent ? ent.device_id : "" };
  }

  setConfig(config) {
    if (!config || (!config.device && !config.entity)) {
      throw new Error("Välj en elev (device) för yclass-kortet");
    }
    this._config = config;
    this._last = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4 + Math.min(this._itemCount || 0, this._config?.max_items || 10);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _render() {
    if (!this._config || !this._hass) return;
    const hass = this._hass;
    const ids = studentEntities(hass, this._config);
    const school = hass.states[ids["sensor.schoolday"]];
    const homework = hass.states[ids["sensor.homework"]];
    const device = hass.devices?.[this._config.device || hass.entities?.[this._config.entity]?.device_id];

    // Only rebuild when something this card shows has changed.
    const key = [school, homework, device, this._expanded].map((o) => (typeof o === "object" ? o?.last_updated ?? o?.id : o));
    key.push(isoDate(new Date()));
    if (this._last && key.every((k, i) => k === this._last[i])) return;
    this._last = key;

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    }

    if (!school && !homework) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="warning">Hittar inga yclass-entiteter för den valda eleven.</div></ha-card>`;
      return;
    }

    const sa = school?.attributes || {};
    const name = sa.first_name || (device?.name_by_user || device?.name || "").split(" ")[0] || "Elev";
    const refDate = sa.reference_date || isoDate(new Date());

    let dayHtml;
    if (sa.date) {
      dayHtml = `
        <div class="day">
          <div class="day-label">${esc(dayLabel(sa.date))}</div>
          <div class="times">
            <div class="time"><span class="label">Börjar</span><span class="value">${esc(sa.start)}</span></div>
            <div class="time"><span class="label">Slutar</span><span class="value">${esc(sa.end)}</span></div>
          </div>
        </div>`;
    } else {
      dayHtml = `<div class="day"><div class="day-label muted">Ingen skoldag inlagd framöver</div></div>`;
    }

    const lunch = sa.date ? sa.lunch || [] : [];
    const lunchHtml = lunch.length
      ? `<div class="lunch">
           <ha-icon icon="mdi:silverware-fork-knife"></ha-icon>
           <ul>${lunch
             .map((dish) => {
               const [main, tag] = splitDish(dish);
               return `<li>${esc(main)}${tag ? ` <span class="tag">${esc(tag)}</span>` : ""}</li>`;
             })
             .join("")}</ul>
         </div>`
      : sa.date
        ? `<div class="lunch muted"><ha-icon icon="mdi:silverware-fork-knife"></ha-icon><span>Ingen matsedel ännu</span></div>`
        : "";

    const max = this._config.max_items || 10;
    const items = (homework?.attributes?.homework || []).filter((h) => String(h.due).slice(0, 10) >= refDate);
    this._itemCount = items.length;
    const hwHtml = items.length
      ? `<ul class="hw">${items
          .slice(0, max)
          .map((h) => {
            const test = h.kind && h.kind.toLowerCase() !== "läxa";
            const open = this._expanded === String(h.id);
            return `
              <li class="${test ? "test" : ""}${open ? " open" : ""}" data-id="${esc(h.id)}">
                <div class="row">
                  <span class="when">${esc(shortDay(h.due))}</span>
                  <span class="what">
                    <span class="title">${esc(h.title)}</span>
                    <span class="meta">${test ? `<span class="chip">${esc(h.kind)}</span>` : ""}${esc(h.subject || "")}</span>
                  </span>
                </div>
                ${
                  open
                    ? `<div class="desc">${esc(h.description || "").replace(/\n/g, "<br>")}
                       ${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noreferrer">Öppna i Vklass</a>` : ""}</div>`
                    : ""
                }
              </li>`;
          })
          .join("")}</ul>${items.length > max ? `<div class="more muted">+ ${items.length - max} till</div>` : ""}`
      : `<div class="empty muted">Inga läxor eller prov framöver 🎉</div>`;

    this.shadowRoot.innerHTML = `${STYLE}
      <ha-card>
        <div class="header">
          <div class="name">${esc(name)}</div>
          <ha-icon icon="mdi:school-outline"></ha-icon>
        </div>
        ${dayHtml}
        ${lunchHtml}
        <div class="section">Läxor och prov</div>
        ${hwHtml}
      </ha-card>`;
  }

  _onClick(ev) {
    if (ev.target.closest("a")) return;
    const li = ev.target.closest("li[data-id]");
    if (!li) return;
    this._expanded = this._expanded === li.dataset.id ? null : li.dataset.id;
    this._render();
  }
}

const STYLE = `<style>
  ha-card { padding: 16px; }
  .muted { color: var(--secondary-text-color); }
  .warning { color: var(--error-color); }
  .header { display: flex; align-items: center; justify-content: space-between; }
  .name { font-size: 1.6em; font-weight: 500; line-height: 1.2; }
  .header ha-icon { color: var(--state-icon-color, var(--primary-color)); }
  .day { margin-top: 12px; }
  .day-label { font-size: 0.95em; color: var(--secondary-text-color); text-transform: none; }
  .times { display: flex; gap: 24px; margin-top: 4px; }
  .time { display: flex; flex-direction: column; }
  .time .label { font-size: 0.8em; color: var(--secondary-text-color); }
  .time .value { font-size: 1.5em; font-weight: 500; font-variant-numeric: tabular-nums; }
  .lunch { display: flex; gap: 10px; align-items: flex-start; margin-top: 14px; }
  .lunch ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: none; margin-top: 1px; }
  .lunch ul { margin: 0; padding: 0; list-style: none; }
  .lunch li { margin-bottom: 2px; }
  .tag { color: var(--secondary-text-color); font-size: 0.85em; }
  .section { margin: 16px 0 6px; font-weight: 500; padding-top: 12px; border-top: 1px solid var(--divider-color); }
  .hw { margin: 0; padding: 0; list-style: none; }
  .hw li { padding: 6px 0; cursor: pointer; border-radius: 6px; }
  .hw li + li { border-top: 1px solid var(--divider-color); }
  .row { display: flex; gap: 12px; align-items: baseline; }
  .when { flex: none; width: 5.5em; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .what { display: flex; flex-direction: column; min-width: 0; }
  .title { overflow-wrap: anywhere; }
  .meta { font-size: 0.85em; color: var(--secondary-text-color); }
  .chip { display: inline-block; margin-right: 6px; padding: 0 6px; border-radius: 8px; font-size: 0.9em;
          font-weight: 500; color: var(--text-primary-color, #fff); background: var(--error-color, #db4437); }
  .test .title { font-weight: 500; }
  .desc { margin: 6px 0 2px calc(5.5em + 12px); font-size: 0.9em; color: var(--secondary-text-color); white-space: normal; }
  .desc a { display: block; margin-top: 4px; color: var(--primary-color); }
  .empty, .more { padding: 6px 0; }
</style>`;

// The integration loads this file early in the frontend's boot. During boot
// HA replaces window.customElements with its own registry (seen in 2026.10),
// and anything defined in the registry it replaces is invisible to dashboards
// ("Custom element doesn't exist"). So wait for HA's registry, recognisable by
// <home-assistant> being defined in it, before defining the card.
async function define() {
  const started = Date.now();
  while (!window.customElements.get("home-assistant") && Date.now() - started < 30000) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const registry = window.customElements;
  if (registry.get("yclass-card")) return;
  registry.define("yclass-card", YclassCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "yclass-card",
    name: "yclass – elev",
    description: "Skoldagens start och slut, lunch och kommande läxor och prov för en elev.",
    preview: true,
    documentationURL: "https://github.com/henriklewander/yclass",
    // HA 2026.10+: offer the card under "Add card → By entity" for any yclass
    // entity, since that tab is where the card picker opens.
    getEntitySuggestion(hass, entityId) {
      const ent = hass.entities?.[entityId];
      if (!ent || ent.platform !== "yclass" || !ent.device_id) return null;
      return { config: { type: "custom:yclass-card", device: ent.device_id } };
    },
  });
  console.info(`%c yclass-card %c ${VERSION} `, "background:#3f51b5;color:#fff", "");
}

define();
