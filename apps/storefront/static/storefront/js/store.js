(function () {
  var lang = document.documentElement.lang === 'en' ? 'en' : 'ar'

  function money(value, unit) {
    return Number(value).toLocaleString('en-US') + ' ' + unit
  }

  var menuButton = document.querySelector('[data-menu]')
  var nav = document.getElementById('site-nav')
  if (menuButton && nav) {
    menuButton.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open')
      menuButton.setAttribute('aria-expanded', open ? 'true' : 'false')
    })
  }

  var slider = document.querySelector('[data-slider]')
  if (slider) {
    var slides = slider.querySelectorAll('.slide')
    var dots = slider.querySelectorAll('[data-to]')
    var index = 0
    var timer = null
    var show = function (next) {
      index = (next + slides.length) % slides.length
      slides.forEach(function (slide, position) {
        var active = position === index
        slide.classList.toggle('is-active', active)
        slide.setAttribute('aria-hidden', active ? 'false' : 'true')
      })
      dots.forEach(function (dot, position) {
        dot.setAttribute('aria-current', position === index ? 'true' : 'false')
      })
    }
    var stop = function () {
      if (timer) clearInterval(timer)
      timer = null
    }
    var start = function () {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || slides.length < 2) return
      stop()
      timer = setInterval(function () { show(index + 1) }, 6500)
    }
    dots.forEach(function (dot) {
      dot.addEventListener('click', function () {
        show(Number(dot.getAttribute('data-to')))
        start()
      })
    })
    slider.addEventListener('mouseenter', stop)
    slider.addEventListener('mouseleave', start)
    slider.addEventListener('focusin', stop)
    show(0)
    start()
  }

  var mainImage = document.querySelector('[data-gallery-main]')
  document.querySelectorAll('[data-thumb]').forEach(function (thumb) {
    thumb.addEventListener('click', function () {
      if (!mainImage) return
      mainImage.src = thumb.getAttribute('data-thumb')
      mainImage.alt = thumb.getAttribute('data-alt') || mainImage.alt
      document.querySelectorAll('[data-thumb]').forEach(function (other) {
        other.setAttribute('aria-current', other === thumb ? 'true' : 'false')
      })
    })
  })

  var form = document.querySelector('[data-order-form]')
  if (!form) return
  var dataNode = document.getElementById('wilaya-data')
  var wilayas = {}
  if (dataNode) {
    JSON.parse(dataNode.textContent).forEach(function (row) { wilayas[row.code] = row })
  }
  var unit = form.getAttribute('data-currency') || (lang === 'en' ? 'DA' : 'دج')
  var pickText = form.getAttribute('data-pick-wilaya') || ''
  var basePrice = Number(form.getAttribute('data-base-price') || 0)
  var fixedSubtotal = form.getAttribute('data-subtotal')
  var quantityInput = form.querySelector('[name=quantity]')
  var wilayaSelect = form.querySelector('[name=wilaya]')
  var communeSelect = form.querySelector('[name=commune]')
  var addressBox = form.querySelector('[data-address]')
  var outputs = {
    product: document.querySelectorAll('[data-sum-product]'),
    delivery: document.querySelectorAll('[data-sum-delivery]'),
    total: document.querySelectorAll('[data-sum-total]'),
    price: document.querySelectorAll('[data-live-price]')
  }

  function setText(nodes, value) {
    nodes.forEach(function (node) { node.textContent = value })
  }

  function currentPrice() {
    var picked = form.querySelector('[name=offer]:checked')
    if (picked) return Number(picked.getAttribute('data-price'))
    return basePrice
  }

  function quantity() {
    if (!quantityInput) return 1
    var max = Number(quantityInput.getAttribute('max') || 10)
    var value = Math.max(1, Math.min(max, parseInt(quantityInput.value, 10) || 1))
    return value
  }

  function deliveryType() {
    var picked = form.querySelector('[name=delivery_type]:checked')
    return picked ? picked.value : 'home'
  }

  function refresh() {
    var subtotal = fixedSubtotal !== null ? Number(fixedSubtotal) : currentPrice() * quantity()
    var row = wilayaSelect ? wilayas[wilayaSelect.value] : null
    var type = deliveryType()
    var fee = null
    if (row) {
      form.querySelectorAll('[name=delivery_type]').forEach(function (radio) {
        radio.disabled = row[radio.value] === null
      })
      if (row[type] === null) {
        var other = type === 'home' ? 'desk' : 'home'
        var otherRadio = form.querySelector('[name=delivery_type][value=' + other + ']')
        if (otherRadio && row[other] !== null) {
          otherRadio.checked = true
          type = other
        }
      }
      fee = row[type]
    }
    if (addressBox) addressBox.hidden = type === 'desk'
    if (fixedSubtotal === null) setText(outputs.price, money(currentPrice(), unit))
    setText(outputs.product, money(subtotal, unit))
    setText(outputs.delivery, fee === null ? pickText : money(fee, unit))
    setText(outputs.total, money(subtotal + (fee || 0), unit))
  }

  function loadCommunes(keep) {
    if (!wilayaSelect || !communeSelect) return
    var code = wilayaSelect.value
    var placeholder = communeSelect.getAttribute('data-placeholder')
    var waiting = communeSelect.getAttribute('data-waiting')
    communeSelect.innerHTML = ''
    var first = document.createElement('option')
    first.value = ''
    first.textContent = code ? placeholder : waiting
    communeSelect.appendChild(first)
    communeSelect.disabled = !code
    if (!code) return
    fetch('/communes/' + code + '.json', { headers: { Accept: 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : { communes: [] } })
      .then(function (data) {
        if (wilayaSelect.value !== code) return
        data.communes.forEach(function (commune) {
          var option = document.createElement('option')
          option.value = commune.ar
          option.textContent = lang === 'ar' ? commune.ar : commune.fr
          if (keep && keep === commune.ar) option.selected = true
          communeSelect.appendChild(option)
        })
      })
      .catch(function () {})
  }

  form.querySelectorAll('[data-step]').forEach(function (button) {
    button.addEventListener('click', function () {
      if (!quantityInput) return
      quantityInput.value = quantity() + Number(button.getAttribute('data-step'))
      quantityInput.value = quantity()
      refresh()
    })
  })
  form.addEventListener('change', function (event) {
    if (event.target === wilayaSelect) loadCommunes('')
    refresh()
  })
  if (quantityInput) quantityInput.addEventListener('input', refresh)
  form.addEventListener('submit', function (event) {
    var submitter = event.submitter
    if (submitter && submitter.hasAttribute('formnovalidate')) return
    document.querySelectorAll('[data-submit]').forEach(function (button) {
      button.disabled = true
    })
  })
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('[data-submit]').forEach(function (button) { button.disabled = false })
  })
  if (wilayaSelect && wilayaSelect.value) {
    loadCommunes(communeSelect ? communeSelect.getAttribute('data-selected') : '')
  }
  refresh()
})()
