(() => {
  document.querySelectorAll("[data-merchant-icon]").forEach((icon) => {
    icon.addEventListener("error", () => {
      const fallback = icon.dataset.fallback;
      if (fallback && icon.src !== fallback) {
        icon.dataset.fallback = "";
        icon.src = fallback;
        return;
      }
      icon.hidden = true;
    });
  });

  const searchInput = document.getElementById("product-search");
  const countLabel = document.getElementById("search-count");
  const noResults = document.getElementById("no-results");
  if (!searchInput || !countLabel || !noResults) return;

  const cards = [...document.querySelectorAll("[data-product-card]")];
  const storeCards = cards.filter((card) => !card.hasAttribute("data-unified-card"));
  const sections = [...document.querySelectorAll("[data-store-section]")];
  const storesView = document.getElementById("view-stores");
  const unifiedView = document.getElementById("view-unified");
  const viewButtons = [...document.querySelectorAll("[data-view]")];
  let currentView = localStorage.getItem("pokemon-dashboard-view") === "unified" ? "unified" : "stores";
  const storeFilters = [...document.querySelectorAll("[data-store-filter]")];
  const kindSelect = document.getElementById("filter-kind");
  const langSelect = document.getElementById("filter-lang");
  const seriesSelect = document.getElementById("filter-series");
  const sortSelect = document.getElementById("sort-by");
  const statusSelect = document.getElementById("filter-status");
  const resetButton = document.getElementById("filter-reset");
  const FILTERS_KEY = "pokemon-dashboard-filters";
  let selectedStore = "all";

  let visibleLimit = 48;

  const fillOptions = (select, key) => {
    const counts = new Map();
    for (const card of storeCards) {
      const value = card.dataset[key];
      if (value) counts.set(value, (counts.get(value) || 0) + 1);
    }
    [...counts.keys()].sort((a, b) => a.localeCompare(b, "fr")).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `${value} (${counts.get(value)})`;
      select.append(option);
    });
  };

  fillOptions(kindSelect, "kind");
  fillOptions(langSelect, "lang");
  fillOptions(seriesSelect, "series");

  const num = (value, fallback) => (value === "" || value === undefined || Number.isNaN(Number(value)) ? fallback : Number(value));
  const comparators = {
    default: (a, b) => num(a.dataset.order, 0) - num(b.dataset.order, 0),
    "price-asc": (a, b) => num(a.dataset.price, Infinity) - num(b.dataset.price, Infinity),
    "price-desc": (a, b) => num(b.dataset.price, -Infinity) - num(a.dataset.price, -Infinity),
    "stock-desc": (a, b) => num(b.dataset.stock, -1) - num(a.dataset.stock, -1),
    recent: (a, b) => num(b.dataset.recent, 0) - num(a.dataset.recent, 0),
    "delta-asc": (a, b) => num(a.dataset.delta, Infinity) - num(b.dataset.delta, Infinity),
  };
  const sortCards = () => {
    const compare = comparators[sortSelect.value] || comparators.default;
    for (const section of [...sections, unifiedView].filter(Boolean)) {
      const grid = section.querySelector(".product-grid");
      const sorted = [...grid.children].sort((a, b) => compare(a, b) || comparators.default(a, b));
      grid.append(...sorted);
    }
  };
  const normalize = (value) => value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
    .trim();

  const applyFilter = (resetLimit = true) => {
    if (resetLimit) {
      visibleLimit = 48;
    }

    const query = normalize(searchInput.value);
    const terms = query.split(/\s+/).filter(Boolean);
    let visibleCount = 0;
    let displayedCount = 0;

    for (const card of cards) {
      const searchable = normalize(card.dataset.search || "");
      const matchesSearch = terms.every((term) => searchable.includes(term));
      const matchesStore = selectedStore === "all"
        || card.dataset.storeIndex === selectedStore;
      const matchesKind = !kindSelect.value || card.dataset.kind === kindSelect.value;
      const matchesLang = !langSelect.value || card.dataset.lang === langSelect.value;
      const matchesSeries = !seriesSelect.value || card.dataset.series === seriesSelect.value;
      const status = statusSelect.value;
      const matchesStatus = !status
        || (status === "deal" ? card.dataset.deal === "1" : card.dataset.status === status);
      const matches = matchesSearch && matchesStore && matchesKind && matchesLang && matchesSeries && matchesStatus;
      
      const inCurrentView = card.hasAttribute("data-unified-card") === (currentView === "unified");

      if (matches && inCurrentView) {
        visibleCount += 1;
        if (displayedCount < visibleLimit) {
          card.hidden = false;
          displayedCount += 1;
        } else {
          card.hidden = true;
        }
      } else {
        card.hidden = true;
      }
    }
    sortCards();
    for (const section of sections) {
      const visibleInSection = [...section.querySelectorAll("[data-product-card]")]
        .some((card) => !card.hidden);
      section.hidden = !visibleInSection;
    }
    noResults.style.display = visibleCount === 0 ? "block" : "none";
    countLabel.textContent = query
      ? `${visibleCount} produit${visibleCount === 1 ? "" : "s"} trouvé${visibleCount === 1 ? "" : "s"}`
      : `${visibleCount} produit${visibleCount === 1 ? "" : "s"} affiché${visibleCount === 1 ? "" : "s"}`;
    
    localStorage.setItem(FILTERS_KEY, JSON.stringify({
      search: searchInput.value,
      kind: kindSelect.value,
      lang: langSelect.value,
      series: seriesSelect.value,
      status: statusSelect.value,
      sort: sortSelect.value,
      store: selectedStore,
    }));
  };

  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}");
    searchInput.value = saved.search || "";
    kindSelect.value = saved.kind || "";
    langSelect.value = saved.lang || "";
    seriesSelect.value = saved.series || "";
    statusSelect.value = saved.status || "";
    sortSelect.value = saved.sort || "default";
    const savedStore = String(saved.store ?? "all");
    if (storeFilters.some((button) => button.dataset.storeFilter === savedStore)) {
      selectedStore = savedStore;
      for (const button of storeFilters) {
        button.setAttribute("aria-pressed", String(button.dataset.storeFilter === savedStore));
      }
    }
  } catch {
    localStorage.removeItem(FILTERS_KEY);
  }

  searchInput.addEventListener("input", () => applyFilter(true));
  for (const select of [kindSelect, langSelect, seriesSelect, sortSelect, statusSelect]) {
    select.addEventListener("change", () => applyFilter(true));
  }
  resetButton.addEventListener("click", () => {
    kindSelect.value = "";
    langSelect.value = "";
    seriesSelect.value = "";
    statusSelect.value = "";
    sortSelect.value = "default";
    searchInput.value = "";
    applyFilter(true);
  });
  for (const filter of storeFilters) {
    filter.addEventListener("click", () => {
      selectedStore = filter.dataset.storeFilter || "all";
      for (const button of storeFilters) {
        button.setAttribute(
          "aria-pressed",
          String(button === filter),
        );
      }
      applyFilter(true);
    });
  }
  const setView = (view) => {
    currentView = view;
    localStorage.setItem("pokemon-dashboard-view", view);
    if (storesView) storesView.hidden = view === "unified";
    if (unifiedView) unifiedView.hidden = view !== "unified";
    for (const button of viewButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    }
    applyFilter(true);
  };
  for (const button of viewButtons) {
    button.addEventListener("click", () => setView(button.dataset.view));
  }
  setView(currentView);

  window.addEventListener("scroll", () => {
    if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 500) {
      visibleLimit += 48;
      applyFilter(false);
    }
  });
})();