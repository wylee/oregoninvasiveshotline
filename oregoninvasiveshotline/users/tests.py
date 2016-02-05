import urllib.parse
from unittest.mock import Mock, patch

from django.conf import settings
from django.core import mail
from django.core.urlresolvers import reverse
from django.test import TestCase

from model_mommy.mommy import make, prepare

from arcutils.test.user import UserMixin

from oregoninvasiveshotline.reports.models import Invite, Report

from .forms import LoginForm, UserForm, UserSearchForm
from .models import User
from .search_indexes import UserIndex


class DetailViewTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.admin = self.create_user(
            username="admin@example.com",
            password="admin",
            is_active=True,
            is_staff=True
        )
        self.inactive_user = self.create_user(
            username="inactive@example.com",
            is_active=False
        )

    def test_permission(self):
        response = self.client.get(reverse("users-detail", args=[self.inactive_user.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse("users-detail", args=[self.user.pk]))
        self.assertEqual(response.status_code, 200)

    def test_get(self):
        self.client.login(email=self.user.email, password="foobar")
        self.client.get(reverse("users-detail", args=[self.user.pk]))


class EditViewTest(TestCase, UserMixin):

    def test_get(self):
        user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True
        )
        self.client.login(email=user.email, password="foo")
        self.client.get(reverse("users-edit", args=[user.pk]))


class AuthenticateViewTest(TestCase, UserMixin):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.login_redirect_url = reverse(settings.LOGIN_REDIRECT_URL)

    def test_bad_signature_redirects_to_home(self):
        response = self.client.get(reverse("users-authenticate") + "?sig=asfd")
        self.assertRedirects(response, reverse("home"))

    def test_active_or_invited_users_are_logged_in(self):
        # test for an invited user
        invite = make(Invite, report=make(Report))
        url = invite.user.get_authentication_url(request=Mock(build_absolute_uri=lambda a: a))
        response = self.client.get(url)
        self.assertRedirects(response, self.login_redirect_url)

        # test for an active user
        user = self.create_user(username="inactive@example.com", is_active=False)
        url = user.get_authentication_url(request=Mock(build_absolute_uri=lambda a: a))
        response = self.client.get(url)
        self.assertRedirects(response, self.login_redirect_url)

    def test_report_ids_session_variable_is_populated(self):
        user = self.create_user(username="foo@example.com", is_active=True)
        report = make(Report, created_by=user)
        url = user.get_authentication_url(request=Mock(build_absolute_uri=lambda a: a))
        response = self.client.get(url)
        self.assertRedirects(response, self.login_redirect_url)
        self.assertIn(report.pk, self.client.session['report_ids'])


class UserHomeViewTest(TestCase):
    def test_fully_anonymous_users_sent_away(self):
        response = self.client.get(reverse("users-home"))
        self.assertRedirects(response, reverse("home"))

    def test_anonymous_user_with_report_ids_session_variable(self):
        # they should be able to see the reports they submitted that are in the
        # session var
        r1 = make(Report)
        r2 = make(Report)
        make(Report)  # this report shouldn't show up in the reported queryset
        session = self.client.session
        session['report_ids'] = [r1.pk, r2.pk]
        session.save()

        response = self.client.get(reverse("users-home"))
        self.assertEqual(2, response.context['reported'])


class UserFormTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.admin = self.create_user(
            username="admin@example.com",
            password="admin",
            is_active=True,
            is_staff=True
        )

    def test_password_field_only_when_user_being_created(self):
        """
        The password field should only be on the form if the user is being
        created (not edited)
        """
        other_user = self.create_user()
        form = UserForm(user=other_user)
        self.assertIn("password", form.fields)

        form = UserForm(instance=self.user, user=other_user)
        self.assertNotIn("password", form.fields)

    def test_your_dangerous_fields_are_not_editable(self):
        """
        Fields like is_active and is_staff should not be changable if the user
        is editing himself
        """
        form = UserForm(user=self.admin, instance=self.admin)
        self.assertNotIn("is_active", form.fields)
        self.assertNotIn("is_staff", form.fields)

        # if someone is editing someone else, they can update those fields
        form = UserForm(instance=self.user, user=self.admin)
        self.assertIn("is_active", form.fields)
        self.assertIn("is_staff", form.fields)

    def test_non_staffers_cannot_set_some_fields(self):
        """
        Some fields should not be editable by non-staffers
        """
        form = UserForm(user=self.user, instance=self.user)
        self.assertNotIn("is_staff", form.fields)

    def test_save_sets_the_password_for_new_users(self):
        """
        If the user is being created, the password should be updated
        """
        # to avoid having to generate valid data for this form, we mock up the
        # superclass's save method and the form's cleaned_data
        with patch("oregoninvasiveshotline.users.forms.forms.ModelForm.save") as mock:
            user = prepare(User)
            form = UserForm(instance=user, user=self.user)
            form.cleaned_data = {"password": "foobar"}

            form.save()

            self.assertTrue(self.user.check_password("foo"))
            # ensure the superclass was called (which actually saves the model)
            self.assertTrue(mock.called)


class UserSearchFormTest(TestCase, UserMixin):
    """
    Tests for the User search form
    """
    def setUp(self):
        self.index = UserIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_search_list_managers_only(self):
        user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        admin = self.create_user(
            username="admin@example.com",
            password="admin",
            is_active=True,
            is_staff=True
        )
        other_user = self.create_user(
            username="other@example.com",
            is_active=False
        )

        form = UserSearchForm({"q": "", "is_manager": True})
        results = form.search()

        # Create a user queryset since form.search() returns a SearchQuerySet
        users = list()
        for u in results:
            users.append(u.object)

        self.assertNotIn(other_user, users)
        self.assertIn(admin, users)
        self.assertIn(user, users)
        self.assertEqual(len(users), 2)


class UserTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            first_name="foo",
            last_name="bar",
            is_active=True,
            is_staff=False
        )
        self.admin = self.create_user(
            username="admin@example.com",
            password="admin",
            is_active=True,
            is_staff=True
        )

    def test_str(self):
        self.assertEqual(str(self.user), "foo bar")
        # the str method should fall back on the email address if a part of
        # their name is blank
        self.user.first_name = ""
        self.user.save()
        self.assertEqual(str(self.user), self.user.email)

    def test_get_full_name(self):
        self.assertEqual(self.user.get_full_name(), "foo bar")

    def test_get_short_name(self):
        self.assertEqual(self.user.get_short_name(), "foo b.")

    def test_has_perm(self):
        """Staff members have all Django admin perms"""
        self.assertFalse(self.user.has_perm("foo"), self.user)
        self.assertTrue(self.admin.has_perm("foo"), self.admin)

    def test_has_module_perms(self):
        """Staff members have all Django admin perms"""
        self.assertFalse(self.user.has_module_perms("foo"), self.user)
        self.assertTrue(self.admin.has_module_perms("foo"), self.admin)

    def test_can_cloak_as(self):
        """Only staffers can cloak"""
        self.assertFalse(self.user.has_module_perms("foo"), self.user)
        self.assertTrue(self.admin.has_module_perms("foo"), self.admin)

    def test_get_proper_name(self):
        user = self.create_user(
            username="asdf",
            prefix="Mr.",
            first_name="Foo",
            last_name="Bar",
            suffix="PHD"
        )
        self.assertEqual(user.get_proper_name(), "Mr. Foo Bar, PHD")

        other_user = self.create_user(
            username="fdsa",
            first_name="Foo",
            last_name="Bar",
            prefix="",
            suffix=""
        )
        self.assertEqual(other_user.get_proper_name(), "Foo Bar")

    def test_get_authentication_url_and_authenticate(self):

        def build_absolute_uri(arg):
            return "http://example.com" + arg

        url = self.user.get_authentication_url(request=Mock(build_absolute_uri=build_absolute_uri), next="lame")
        parts = urllib.parse.urlparse(url)
        self.assertEqual(parts.path, reverse("users-authenticate"))
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(query['next'][0], "lame")
        self.assertEqual(self.user, User.authenticate(query['sig'][0]))


class LoginFormTest(TestCase, UserMixin):
    def test_clean_email_raises_validation_error_for_non_existing_user(self):
        form = LoginForm({
            "email": "foo@pdx.edu"
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error("email"))

    def test_save_sends_email_to_user_with_login_link(self):
        user = self.create_user(username="foo@example.com")
        form = LoginForm({
            "email": user.email
        })
        self.assertTrue(form.is_valid())
        with patch("oregoninvasiveshotline.users.forms.User.get_authentication_url", return_value="foobarius"):
            form.save(request=Mock())
            self.assertTrue(len(mail.outbox), 1)
            self.assertIn("foobarius", mail.outbox[0].body)


class LoginViewTest(TestCase, UserMixin):
    def test_get(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_logging_in_via_email_sends_an_email(self):
        user = self.create_user(username="foo@example.com")
        response = self.client.post(reverse("login"), {
            "email": user.email,
            "form": "OTHER_LOGIN",
        })
        self.assertTrue(len(mail.outbox), 1)
        self.assertRedirects(response, reverse("login"))
