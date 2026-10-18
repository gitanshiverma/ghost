(function () {
  var form = document.getElementById('scholarship-form');
  var confirmation = document.getElementById('confirmation');

  function setError(id, msg) {
    var el = document.getElementById(id);
    var err = document.getElementById(id + '-error');
    if (err) err.textContent = msg || '';
    if (el && el.type !== 'checkbox' && el.type !== 'file') {
      if (msg) { el.classList.add('invalid'); el.setAttribute('aria-invalid', 'true'); }
      else { el.classList.remove('invalid'); el.removeAttribute('aria-invalid'); }
    }
    return !msg;
  }

  function val(id) { return document.getElementById(id).value.trim(); }

  var checks = {
    'full-name': function () {
      var v = val('full-name');
      if (!v) return 'Enter your full legal name.';
      if (v.length < 3 || v.indexOf(' ') === -1) return 'Enter your first and last name.';
      return '';
    },
    'university-email': function () {
      var v = val('university-email');
      if (!v) return 'Enter your university email address.';
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return 'Enter a valid email address, for example name@northbridge.edu.';
      return '';
    },
    'student-id': function () {
      var v = val('student-id');
      if (!v) return 'Enter your student ID.';
      if (!/^[A-Za-z0-9-]{5,15}$/.test(v)) return 'Student ID must be 5 to 15 letters or numbers.';
      return '';
    },
    'degree-programme': function () { return val('degree-programme') ? '' : 'Enter your degree programme.'; },
    'current-year': function () { return val('current-year') ? '' : 'Select your current year of study.'; },
    'scholarship-category': function () { return val('scholarship-category') ? '' : 'Select a scholarship category.'; },
    'academic-achievements': function () { return val('academic-achievements') ? '' : 'Describe your academic achievements.'; },
    'personal-statement': function () {
      var v = val('personal-statement');
      if (!v) return 'Write a personal statement.';
      if (v.length < 50) return 'Your personal statement must be at least 50 characters.';
      return '';
    },
    'academic-transcript': function () {
      var f = document.getElementById('academic-transcript').files[0];
      if (!f) return 'Attach your academic transcript.';
      if (f.size > 5 * 1024 * 1024) return 'File is larger than 5 MB.';
      return '';
    },
    'supporting-document': function () {
      var f = document.getElementById('supporting-document').files[0];
      if (f && f.size > 5 * 1024 * 1024) return 'File is larger than 5 MB.';
      return '';
    },
    'declaration': function () {
      return document.getElementById('declaration').checked ? '' : 'You must confirm the declaration before submitting.';
    }
  };

  function validateField(id) { return setError(id, checks[id]()); }

  Object.keys(checks).forEach(function (id) {
    var el = document.getElementById(id);
    el.addEventListener('blur', function () { validateField(id); });
    el.addEventListener('change', function () { validateField(id); });
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var firstBad = null;
    Object.keys(checks).forEach(function (id) {
      if (!validateField(id) && !firstBad) firstBad = id;
    });
    if (firstBad) {
      confirmation.hidden = true;
      document.getElementById(firstBad).focus();
      return;
    }
    // Local simulation only: nothing is sent anywhere.
    document.getElementById('reference-number').textContent =
      'NB-' + new Date().getFullYear() + '-' + Math.floor(100000 + Math.random() * 900000);
    confirmation.hidden = false;
    confirmation.focus();
    confirmation.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  document.getElementById('reset-button').addEventListener('click', function () {
    form.reset();
    Object.keys(checks).forEach(function (id) { setError(id, ''); });
    confirmation.hidden = true;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
})();
