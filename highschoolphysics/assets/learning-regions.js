/* View-only controls: search and column selection never write learning records. */
(() => {
  document.querySelectorAll('.map-module-switcher').forEach(details => {
    const narrow = matchMedia('(max-width:700px)');
    details.open = !narrow.matches;
    narrow.addEventListener('change', () => { details.open = !narrow.matches; });
  });
  document.querySelectorAll('[data-region-search]').forEach(input => {
    const directory = input.closest('.region-directory');
    const groups = [...directory.querySelectorAll(':scope > details')];
    const status = directory.querySelector('[role="status"]');
    const original = new Map(groups.map(group => [group, group.open]));
    input.closest('label').hidden = false;
    input.addEventListener('input', () => {
      const query = input.value.trim().toLocaleLowerCase();
      let found = 0;
      groups.forEach(group => {
        let visible = 0;
        group.querySelectorAll('[data-region-name]').forEach(row => {
          row.hidden = !row.dataset.regionName.toLocaleLowerCase().includes(query);
          if (!row.hidden) visible++;
        });
        group.hidden = !visible;
        group.open = query ? !!visible : original.get(group);
        found += visible;
      });
      status.hidden = !query;
      status.textContent = found ? `找到 ${found} 个学习区域` : '没有匹配的区域，请换一个关键词或清空搜索。';
    });
  });
  document.querySelectorAll('[data-matrix-view]').forEach(select => {
    const map = select.closest('.learning-map');
    const table = map.querySelector('.region-matrix');
    const caption = table.querySelector('caption');
    const fullCaption = caption.textContent;
    select.closest('label').hidden = false;
    const stage = new URLSearchParams(location.search).get('stage');
    const options = [...select.options].map(option => option.value);
    select.value = options.includes(stage) ? stage : matchMedia('(max-width:900px)').matches ? 'first' : 'all';
    const update = () => {
      table.querySelectorAll('[data-stage]').forEach(cell => {
        cell.hidden = select.value !== 'all' && cell.dataset.stage !== select.value;
      });
      table.classList.toggle('region-matrix-single', select.value !== 'all');
      caption.textContent = select.value === 'all' ? fullCaption : select.selectedOptions[0].textContent + '（受阻人数 / 证据足够人数）';
    };
    select.addEventListener('change', update);
    update();
  });
  document.querySelectorAll('.region-filters [name="class_id"]').forEach(select => {
    // Assessment options belong to the currently displayed class. Clear on explicit class change.
    select.addEventListener('change', () => {
      select.form.querySelector('[name="assessment_id"]').value = '';
    });
  });
})();
