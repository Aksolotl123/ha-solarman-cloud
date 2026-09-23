# Solarman Cloud (account login) — Home Assistant

Integracja Home Assistant, która pobiera dane instalacji fotowoltaicznej z **chmury
Solarman**, logując się **tym samym kontem co aplikacja Solarman Smart**. Nie wymaga, aby Home Assistant i falownik były w tej samej sieci — dane idą
przez chmurę, tak jak w aplikacji na telefon.

Integracja ma **dwa tryby logowania**, wybierane przy dodawaniu:

1. **Oficjalne API (zalecane)** — App ID i App Secret, o które prosi się mailowo
   Solarman (`customerservice@solarmanpv.com`), oraz e-mail i hasło konta
   SOLARMAN Smart. Integracja loguje się sama, bez captchy, bez tokenu z przeglądarki
   i bez daty ważności. Zapisywany jest tylko skrót SHA-256 hasła.
   Dla kont Smart API jest darmowe przy ≤3 instalacjach i ≤200 000 wywołań rocznie.
2. **Token z portalu (zapasowo)** — prywatne, nieudokumentowane API portalu
   SolarmanPV (`grant_type=refresh_token`), opisane niżej. Może przestać działać
   po zmianach po stronie Solarman.

Tryb istniejącej instalacji zmienisz w **Ustawienia → Urządzenia i usługi →
Solarman Cloud → ⋮ → Skonfiguruj ponownie**. Encje i ich historia zostają te same.

## Oficjalne API

Adres domyślny to `https://globalapi.solarmanpv.com` (konta spoza Chin — taki
podaje Solarman w mailu z kluczem). Konta chińskiej chmury używają
`https://api.solarmanpv.com`. Oficjalne API nie zawsze podaje sumy dzienne,
miesięczne i całkowite w danych bieżących — wtedy integracja liczy je z historii
produkcji (dzień po dniu i miesiąc po miesiącu), więc encje są te same w obu trybach.
Encje „Token ważny do” i „Adres webhooka tokenu” istnieją tylko w trybie tokenu.

## Tryb tokenu z portalu — jak działa uwierzytelnianie

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
  - Produkcja dziś / wczoraj / w tym miesiącu / w zeszłym miesiącu / całkowita `[kWh]`
    (dziś / w miesiącu / całkowita są gotowe do panelu **Energia**)
  - *Produkcja wczoraj* pochodzi z historii dziennej w chmurze — dane z falownika
    znikają z bieżącego podsumowania o północy
  - Stan baterii `[%]` (jeśli masz magazyn)
  - Temperatura, status sieci, czas ostatniej aktualizacji (diagnostyka)
- Import **historii miesięcznej** z chmury do statystyk Home Assistanta —
  wykres produkcji miesiąc po miesiącu od początku działania instalacji,
  a nie od dnia instalacji integracji (zob. niżej).

## Wykres produkcji miesiąc po miesiącu

Home Assistant zna tylko to, co sam zapisał, więc świeżo zainstalowana
integracja miałaby wykres zaczynający się dzisiaj. Solarman pamięta wszystkie
miesiące od uruchomienia instalacji, więc integracja pobiera tę historię
(`/maintain-s/history/power/<id>/stats/year`) i zapisuje ją jako **statystykę
zewnętrzną**:

```
solarman_cloud:station_<ID_INSTALACJI>_production_monthly
```

Jeden punkt na miesiąc, z sumą narastającą — Home Assistant rysuje z tego
słupek na miesiąc. Historia jest odświeżana przy każdym odpytaniu (bieżący
miesiąc rośnie), a zakończone lata pobierane są tylko raz, przy starcie.

Aby zobaczyć wykres, dodaj do dashboardu kartę (*Dodaj kartę* → *Ręcznie*):

```yaml
type: statistics-graph
title: Produkcja miesięczna
entities:
  - solarman_cloud:station_64944995_production_monthly   # podmień ID instalacji
period: month
stat_types:
  - change
chart_type: bar
days_to_show: 1095
```

ID instalacji znajdziesz w adresie encji lub w logu integracji. Ta statystyka
jest celowo **osobna** od sensora produkcji — nie dodawaj jej do panelu
**Energia**, bo produkcja liczyłaby się podwójnie.

### Ta sama historia w szablonach

Szablony Jinja (powiadomienia, karta markdown) **nie mają dostępu do statystyk**,
dlatego te same liczby są wystawione jako atrybut `months` sensora *Produkcja
w zeszłym miesiącu* — słownik `{"RRRR-MM": kWh}`, ostatnie 24 miesiące, od
najstarszego. Przykład: ostatnie 12 miesięcy w powiadomieniu:

```jinja
{% set m = state_attr('sensor.<twoj>_produkcja_w_zeszlym_miesiacu', 'months') or {} %}
{% for ym, kwh in (m.items() | list)[-12:] %}
{{ ym[5:7] }}.{{ ym[:4] }}  {{ '%.2f' | format(kwh) }} kWh
{% endfor %}
```

## Instalacja przez HACS

1. HACS → menu (trzy kropki) → **Custom repositories**.
2. Wklej adres tego repozytorium, kategoria **Integration**, **Add**.
3. Znajdź *Solarman Cloud (account login)*, **Download**, zrestartuj Home Assistant.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację** → *Solarman Cloud*.
5. Wklej token odświeżania, podaj kod regionu (np. `PL`) i adres bazowy API.

### Instalacja ręczna

Skopiuj katalog `custom_components/solarman_cloud/` do `config/custom_components/`
w swojej instancji Home Assistant i zrestartuj.

## Region i adres API (tryb tokenu)

Domyślnie `https://home.solarmanpv.com` (Europa, w tym Polska). Jeśli w aplikacji
logujesz się do innego regionu, może być potrzebny inny host (np.
`https://globalhome.solarmanpv.com`) i inny kod regionu.

## Sterowanie / dane lokalne

Ta integracja jest **tylko do odczytu**. Do sterowania falownikiem i danych co kilka
sekund służy lokalna integracja
[`davidrapan/ha-solarman`](https://github.com/davidrapan/ha-solarman) (wymaga dostępu
sieciowego do loggera na porcie 8899).

## Licencja

MIT.
