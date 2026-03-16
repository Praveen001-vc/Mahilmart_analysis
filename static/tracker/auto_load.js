document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-auto-load-table]").forEach(initializeAutoLoadTable);
});

function initializeAutoLoadTable(section) {
    const tableBody = section.querySelector("[data-table-body]");
    const sentinel = section.querySelector("[data-load-sentinel]");
    const status = section.querySelector("[data-load-status]");
    const nextLink = section.querySelector("[data-next-link]");

    if (!tableBody || !sentinel || !status) {
        return;
    }

    const totalCount = Number(section.dataset.totalCount || tableBody.rows.length || 0);
    const batchSize = Number(section.dataset.batchSize || 50);
    let nextUrl = section.dataset.nextUrl || (nextLink ? nextLink.href : "");
    let loading = false;

    const setStatus = (message) => {
        status.textContent = message;
    };

    const syncDisplay = () => {
        const visibleCount = tableBody.querySelectorAll("tr").length;

        if (nextLink) {
            if (nextUrl) {
                nextLink.href = nextUrl;
                nextLink.hidden = false;
            } else {
                nextLink.hidden = true;
            }
        }

        if (!visibleCount) {
            setStatus("No records to display.");
            return;
        }

        if (nextUrl) {
            setStatus(
                `Showing ${visibleCount} of ${totalCount} records. Scroll down to load the next ${batchSize}.`
            );
            return;
        }

        setStatus(`Showing all ${visibleCount} records.`);
    };

    const loadNextPage = async () => {
        if (!nextUrl || loading) {
            return;
        }

        loading = true;
        section.classList.add("is-loading");
        setStatus(`Loading next ${batchSize} records...`);

        try {
            const response = await fetch(nextUrl, {
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
            });

            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }

            const html = await response.text();
            const documentFragment = new DOMParser().parseFromString(html, "text/html");
            const nextSection = documentFragment.querySelector("[data-auto-load-table]");

            if (!nextSection) {
                throw new Error("Could not find the next table page.");
            }

            nextSection
                .querySelectorAll("[data-table-body] tr")
                .forEach((row) => tableBody.appendChild(row));

            nextUrl = nextSection.dataset.nextUrl || "";
            section.dataset.nextUrl = nextUrl;
            syncDisplay();
        } catch (error) {
            setStatus("Could not load more records automatically. Use the button to try again.");
            if (nextLink) {
                nextLink.hidden = false;
            }
        } finally {
            loading = false;
            section.classList.remove("is-loading");
        }
    };

    if (nextLink) {
        nextLink.addEventListener("click", (event) => {
            event.preventDefault();
            loadNextPage();
        });
    }

    syncDisplay();

    if (!nextUrl || !("IntersectionObserver" in window)) {
        return;
    }

    const observer = new IntersectionObserver(
        (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
                loadNextPage();
            }
        },
        {
            rootMargin: "200px 0px",
        }
    );

    observer.observe(sentinel);
}
