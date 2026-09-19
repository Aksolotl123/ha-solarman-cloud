# Solarman Cloud (account login) — Home Assistant

Integracja Home Assistant, która pobiera dane instalacji fotowoltaicznej z **chmury
Solarman**, logując się **tym samym kontem (e-mail + hasło) co aplikacja Solarman
Smart**. Nie wymaga, aby Home Assistant i falownik były w tej samej sieci — dane idą
przez chmurę, tak jak w aplikacji na telefon.

> **Uwaga:** integracja korzysta z prywatnego, nieudokumentowanego API portalu
> SolarmanPV (`grant_type=mdc_password`). Może przestać działać po zmianach po
> stronie Solarman i prawdopodobnie wykracza poza oficjalny regulamin. Oficjalną,
> wspieraną drogą jest OpenAPI z App ID / App Secret (do uzyskania mailowo od
> Solarman) — zob. sekcję *Alternatywa* poniżej.

## Funkcje

- Logowanie kontem Solarman (e-mail + hasło), bez App ID / App Secret.
- Odczyt danych na poziomie instalacji, domyślnie co **1 godzinę** (konfigurowalne,
  min. 5 min — chmura i tak odświeża co ~5 min).
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
5. Podaj e-mail, hasło, kod regionu (np. `PL`) i adres bazowy API
   (domyślnie `https://home.solarmanpv.com`). Wybierz instalację.

Interwał odpytywania zmienisz później w **Konfiguruj** przy integracji.

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
