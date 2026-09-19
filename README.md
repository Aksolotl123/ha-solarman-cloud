# Solarman Cloud (account login) — Home Assistant

Integracja Home Assistant, która pobiera dane instalacji fotowoltaicznej z **chmury
Solarman**, logując się **tym samym kontem co aplikacja Solarman Smart**. Nie wymaga, aby Home Assistant i falownik były w tej samej sieci — dane idą
przez chmurę, tak jak w aplikacji na telefon.

> **Uwaga:** integracja korzysta z prywatnego, nieudokumentowanego API portalu
> SolarmanPV (`grant_type=refresh_token`). Może przestać działać po zmianach po
> stronie Solarman i prawdopodobnie wykracza poza oficjalny regulamin. Oficjalną,
> wspieraną drogą jest OpenAPI z App ID / App Secret (do uzyskania mailowo od
> Solarman) — zob. sekcję *Alternatywa* poniżej.

## Jak działa uwierzytelnianie (ważne)

Solarman chroni logowanie hasłem **captchą z suwakiem** — zapytanie wysłane przez
skrypt dostaje `HTTP 412 AUTH_SLIDE_ERROR`. Dlatego integracja **nie loguje się
hasłem**. Zamiast tego logujesz się raz sam w przeglądarce (rozwiązując suwak),
a integracja dostaje od Ciebie **token odświeżania** i dalej odnawia dostęp sama
grantem `refresh_token`, który captchy nie wymaga.

Token jest **rotowany przy każdym odświeżeniu** — integracja zapisuje nowy
automatycznie. Gdy token przestanie działać, Home Assistant poprosi o wklejenie
nowego (ponowna autoryzacja).

### Skąd wziąć token

1. Zaloguj się na `https://home.solarmanpv.com` w przeglądarce.
2. Otwórz narzędzia deweloperskie (F12) → zakładka **Application** / **Aplikacja**
   → **Cookies** → `https://home.solarmanpv.com`.
3. Skopiuj **wartość** ciasteczka o nazwie `442287045fabeaa868450dec4baee7f4`
   (to jest refresh token).

Alternatywnie w konsoli przeglądarki (zakładka **Console**):

```js
copy(decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('442287045fabeaa868450dec4baee7f4=')).split('=').slice(1).join('=')))
```

To skopiuje token do schowka bez wyświetlania go na ekranie.

### Automatyczne przekazywanie tokenu (opcjonalne)

Żeby nie kopiować tokenu ręcznie, integracja wystawia **webhook**, a w przeglądarce
działa **userscript** (Tampermonkey). Po Twoim zalogowaniu na portalu skrypt sam
wykrywa token i wysyła go do Home Assistant — integracja weryfikuje go, zapisuje
i przeładowuje się. Ty logujesz się i rozwiązujesz suwak sam; automatyzowane jest
wyłącznie przeniesienie tokenu.

1. Po starcie integracji znajdź w logu HA linię `Solarman token webhook ready` —
   zawiera Twój prywatny adres webhooka.
2. Zainstaluj rozszerzenie Tampermonkey i dodaj skrypt
   [`userscript/solarman-token-to-ha.user.js`](userscript/solarman-token-to-ha.user.js).
3. Wklej adres webhooka w miejsce `PASTE_YOUR_WEBHOOK_URL_HERE`.

**Adres webhooka traktuj jak hasło** — kto go zna, może wysłać token do Twojego HA.

## Funkcje

- Odczyt danych na poziomie instalacji, domyślnie co **1 godzinę** (konfigurowalne,
  min. 5 min — chmura i tak odświeża co ~5 min).
- Automatyczne odnawianie i zapisywanie rotowanego tokenu.
- Ponowna autoryzacja przez UI, gdy token wygaśnie.
- Sensory (tworzone tylko, jeśli dane są dostępne dla Twojej instalacji):
  - Bieżąca produkcja `[W]`, bieżące zużycie `[W]`
  - Moc sieci / pobór z sieci `[W]`
  - Produkcja dziś / w miesiącu / całkowita `[kWh]` (gotowe do panelu **Energia**)
  - Stan baterii `[%]` (jeśli masz magazyn)
  - Temperatura, status sieci, czas ostatniej aktualizacji (diagnostyka)

## Instalacja przez HACS

1. HACS → menu (trzy kropki) → **Custom repositories**.
2. Wklej adres tego repozytorium, kategoria **Integration**, **Add**.
3. Znajdź *Solarman Cloud (account login)*, **Download**, zrestartuj Home Assistant.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację** → *Solarman Cloud*.
5. Wklej token odświeżania, podaj kod regionu (np. `PL`) i adres bazowy API.

### Instalacja ręczna

Skopiuj katalog `custom_components/solarman_cloud/` do `config/custom_components/`
w swojej instancji Home Assistant i zrestartuj.

## Region i adres API

Domyślnie `https://home.solarmanpv.com` (Europa, w tym Polska). Jeśli w aplikacji
logujesz się do innego regionu, może być potrzebny inny host (np.
`https://globalhome.solarmanpv.com`) i inny kod regionu.

## Alternatywa: oficjalne OpenAPI

Jeśli zależy Ci na stabilności, napisz do `customerservice@solarmanpv.com` z prośbą
o **App ID / App Secret** i użyj integracji opartej na oficjalnym API, np.
[`norberttech/ha-solarman-api`](https://github.com/norberttech/ha-solarman-api).

## Sterowanie / dane lokalne

Ta integracja jest **tylko do odczytu**. Do sterowania falownikiem i danych co kilka
sekund służy lokalna integracja
[`davidrapan/ha-solarman`](https://github.com/davidrapan/ha-solarman) (wymaga dostępu
sieciowego do loggera na porcie 8899).

## Licencja

MIT.
