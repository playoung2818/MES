// Adapted from playoung2818/MRP_System PRODUCTION_TPL (f37fe90).

    var pendingScheduleChanges = {};
    var scheduleSaveButton = document.getElementById("schedule-save");
    var scheduleSaveMsg = document.getElementById("schedule-save-msg");

    function setScheduleSaveMessage(text, isError){
      if (!scheduleSaveMsg) {
        return;
      }
      scheduleSaveMsg.textContent = text || "";
      scheduleSaveMsg.style.color = isError ? "#b91c1c" : "#6b7280";
    }

    function updateScheduleSaveState(){
      var count = Object.keys(pendingScheduleChanges).length;
      if (scheduleSaveButton) {
        scheduleSaveButton.disabled = count === 0;
      }
      if (count > 0) {
        setScheduleSaveMessage(count + " unsaved", false);
      } else {
        setScheduleSaveMessage("", false);
      }
    }

    document.querySelectorAll(".order-line[data-wo]").forEach(function(row){
      var woNumber = row.getAttribute("data-wo");
      var originalDate = row.getAttribute("data-production-date") || "";
      var originalArea = row.closest("[data-target-area='finished_goods']") ? "finished_goods" : "schedule";
      var currentArea = originalArea;
      var returnDate = originalDate || row.getAttribute("data-return-production-date") || "";
      function productionReturnDate(){
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        var date = returnDate ? new Date(returnDate + "T00:00:00") : today;
        if (isNaN(date.getTime()) || date < today) date = today;
        while (date.getDay() === 0 || date.getDay() === 6) date.setDate(date.getDate() + 1);
        return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
      }
      var control = document.createElement("div");
      control.className = "production-date-control";
      var label = document.createElement("label");
      label.textContent = "Production date ";
      var input = document.createElement("input");
      input.type = "date";
      input.className = "production-date-input";
      function refreshMinimumDate(){
        var now = new Date();
        input.min = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
      }
      refreshMinimumDate();
      input.addEventListener("focus", refreshMinimumDate);
      input.value = originalDate;
      input.setAttribute("aria-label", "Production date for " + woNumber);
      label.appendChild(input);
      control.appendChild(label);
      var finishButton = document.createElement("button");
      finishButton.type = "button";
      finishButton.className = "btn btn-sm btn-outline-secondary";
      function updateFinishButton(){
        finishButton.textContent = currentArea === "finished_goods" ? "It's not Finished!" : "Move to FG";
      }
      updateFinishButton();
      control.appendChild(finishButton);
      var message = document.createElement("span");
      message.className = "production-date-msg";
      message.setAttribute("role", "status");
      control.appendChild(message);
      row.appendChild(control);

      function stage(area, date){
        currentArea = area;
        if (area === "schedule" && date) returnDate = date;
        updateFinishButton();
        var changed = area !== originalArea || date !== originalDate;
        if (changed) {
          pendingScheduleChanges[woNumber] = {target_area: area, production_date: date};
        } else {
          delete pendingScheduleChanges[woNumber];
        }
        row.classList.toggle("schedule-unsaved", changed);
        message.textContent = changed ? (area === "finished_goods" ? "Unsaved: Finish Goods" : "Unsaved: " + date) : "";
        updateScheduleSaveState();
      }
      input.addEventListener("change", function(){
        input.setCustomValidity("");
        refreshMinimumDate();
        var date = input.value;
        if (date) {
          var day = new Date(date + "T00:00:00").getDay();
          if (date < input.min || day === 0 || day === 6) {
            input.setCustomValidity(date < input.min ? "Choose today or a future weekday for production." : "Choose a weekday for production.");
            input.reportValidity();
            var previous = pendingScheduleChanges[woNumber];
            input.value = previous ? previous.production_date : originalDate;
            input.setCustomValidity("");
            return;
          }
          stage("schedule", date);
        } else {
          input.value = originalDate;
          stage(originalArea, originalDate);
        }
      });
      finishButton.addEventListener("click", function(){
        if (currentArea === "finished_goods") {
          input.value = productionReturnDate();
          stage("schedule", input.value);
        } else {
          input.value = "";
          stage("finished_goods", "");
        }
      });
    });
    window.addEventListener("beforeunload", function(event){
      if (Object.keys(pendingScheduleChanges).length) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    if (scheduleSaveButton) {
      scheduleSaveButton.addEventListener("click", function(){
        var assignments = Object.keys(pendingScheduleChanges).map(function(woNumber){
          var change = pendingScheduleChanges[woNumber] || {};
          return {
            wo_number: woNumber,
            target_area: change.target_area || "schedule",
            production_date: change.production_date || ""
          };
        });
        if (!assignments.length) {
          return;
        }
        document.querySelectorAll(".production-date-control input, .production-date-control button").forEach(function(el){ el.disabled = true; });
        scheduleSaveButton.disabled = true;
        scheduleSaveButton.textContent = "Saving";
        setScheduleSaveMessage("", false);
        fetch("/api/production_schedule", {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-CSRFToken": document.getElementById("planning-csrf").value},
          body: JSON.stringify({assignments: assignments})
        })
          .then(function(resp){
            return resp.json().then(function(data){ return {ok: resp.ok, data: data}; });
          })
          .then(function(result){
            if (!result.ok || !result.data.ok) {
              throw new Error(result.data.error || "Schedule save failed");
            }
            pendingScheduleChanges = {};
            document.querySelectorAll(".schedule-unsaved").forEach(function(row){
              row.classList.remove("schedule-unsaved");
            });
            scheduleSaveButton.textContent = "Save Schedule";
            updateScheduleSaveState();
            setScheduleSaveMessage("Saved", false);
            window.location.reload();
          })
          .catch(function(err){
            scheduleSaveButton.disabled = false;
            scheduleSaveButton.textContent = "Save Schedule";
            document.querySelectorAll(".production-date-control input, .production-date-control button").forEach(function(el){ el.disabled = false; });
            setScheduleSaveMessage(err.message || "Schedule save failed", true);
          });
      });
    }
  