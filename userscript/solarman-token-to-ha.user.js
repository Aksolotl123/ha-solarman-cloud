// ==UserScript==
// @name         Solarman -> Home Assistant token
// @namespace    solarman-cloud-ha
// @version      1.0
// @description  After you sign in to SolarmanPV yourself, send the refresh token to Home Assistant so the integration keeps working without manual copying.
// @match        https://home.solarmanpv.com/*
// @match        https://globalhome.solarmanpv.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      *
// @run-at       document-idle
// ==/UserScript==

/*
 * SETUP: replace WEBHOOK_URL below with your own webhook address.
 * You will find it in the Home Assistant log right after the integration starts,
 * on a line reading "Solarman token webhook ready".
 * It looks like:  https://<your-ha-address>/api/webhook/<long-random-id>
 *
 * Treat that URL as a secret: anyone who has it can push a token to your HA.
 *
 * This script does NOT log you in and does NOT touch the CAPTCHA. You sign in
 * yourself; it only forwards the token that your own session already holds.
 */
const WEBHOOK_URL = 'PASTE_YOUR_WEBHOOK_URL_HERE';

// Cookie holding the refresh token in the SolarmanPV web portal.
const COOKIE_NAME = '442287045fabeaa868450dec4baee7f4';

(function () {
  'use strict';

  if (!WEBHOOK_URL || WEBHOOK_URL.indexOf('PASTE_YOUR') === 0) {
    console.warn('[Solarman->HA] WEBHOOK_URL is not set; edit the userscript.');
    return;
  }

  function readCookie(name) {
    const row = document.cookie
      .split('; ')
      .find((c) => c.startsWith(name + '='));
    if (!row) return null;
    return decodeURIComponent(row.split('=').slice(1).join('='));
  }

  function send(token) {
    GM_xmlhttpRequest({
      method: 'POST',
      url: WEBHOOK_URL,
      data: token,
      headers: { 'Content-Type': 'text/plain' },
      onload: (res) => {
        if (res.status === 200) {
          GM_setValue('lastSent', token);
          console.info('[Solarman->HA] Token delivered:', res.responseText);
        } else {
          console.warn('[Solarman->HA] HA replied', res.status, res.responseText);
        }
      },
      onerror: () => console.warn('[Solarman->HA] Could not reach Home Assistant.'),
    });
  }

  function check() {
    const token = readCookie(COOKIE_NAME);
    if (!token) return; // not signed in yet
    if (token === GM_getValue('lastSent', '')) return; // already delivered
    send(token);
  }

  // Run once now, then watch for a token appearing after a fresh sign-in.
  check();
  let ticks = 0;
  const timer = setInterval(() => {
    check();
    if (++ticks > 60) clearInterval(timer); // give up after ~5 minutes
  }, 5000);
})();
