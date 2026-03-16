document.addEventListener("DOMContentLoaded", () => {
    const clampPercentage = (value) => {
        const numericValue = Number.parseFloat(String(value || "0").trim());
        if (!Number.isFinite(numericValue)) {
            return 0;
        }
        return Math.min(100, Math.max(0, numericValue));
    };

    document
        .querySelectorAll("[data-dashboard-income-height]")
        .forEach((element) => {
            element.style.height = `${clampPercentage(
                element.dataset.dashboardIncomeHeight,
            )}%`;
        });

    document
        .querySelectorAll("[data-dashboard-expense-height]")
        .forEach((element) => {
            element.style.height = `${clampPercentage(
                element.dataset.dashboardExpenseHeight,
            )}%`;
        });

    document.querySelectorAll("[data-dashboard-progress]").forEach((element) => {
        element.style.setProperty(
            "--progress",
            clampPercentage(element.dataset.dashboardProgress),
        );
    });

    const dateInput = document.querySelector("[data-dashboard-date-input]");
    if (!dateInput) {
        return;
    }

    dateInput.addEventListener("change", () => {
        dateInput.form.requestSubmit();
    });
});
